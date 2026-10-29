import csv
import json
import math
from pathlib import Path
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_devices import seed_user, make_session_token
import test_uwb_setup as touch_tests
from petsense import db
from petsense.db.models import Device
from petsense.uwb import setup, setup_jobs
from petsense.uwb.maps import Registry
from petsense.uwb.service import PositionService


class WorkflowTests(unittest.TestCase):
    setUp = touch_tests.UwbSetupTests.setUp
    frame = touch_tests.UwbSetupTests.frame

    def walk_frame(self, xy, seq):
        frame = self.frame(0, seq % 256)
        frame['uwb'].update(timer=seq, anchorMask=7,
            distanceMm=[math.dist(xy, anchor) * 1000 for anchor in [[0, 0], [4, 0], [0, 3]]])
        return frame

    def prepare_walk(self):
        self.state.update(anchors=[[0, 0], [4, 0], [0, 3]], phase='anchors_ready')
        self.store.save(self.identity, self.state)
        self.store.start_walk(self.identity, self.state)

    def test_walk_logs_ranges_and_rejects_duplicates_stale_and_incomplete_frames(self):
        self.prepare_walk()
        frame = self.walk_frame([1, 1], 1)
        self.store.ingest(self.identity, frame)
        self.store.ingest(self.identity, frame)
        invalid = self.walk_frame([1, 1], 2)
        invalid['uwb']['anchorMask'] = 6
        self.store.ingest(self.identity, invalid)
        invalid = self.walk_frame([1, 1], 3)
        invalid['ts_ms'] = 5000
        self.store.ingest(self.identity, invalid)
        state = self.store.read(self.identity)
        self.assertEqual(state['walk']['points'], 1)
        folder = self.store.folder(self.identity) / 'walks' / state['walk']['id']
        with (folder / 'trail.csv').open(encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertAlmostEqual(float(rows[0]['raw_x_m']), 1)
        self.assertAlmostEqual(float(rows[0]['raw_y_m']), 1)
        self.assertTrue((folder / 'ranges.ndjson').is_file())
        self.store.stop_walk(self.identity, state)
        self.assertEqual(state['phase'], 'anchors_ready')

    def test_walk_restart_retry_preserve_raw_and_touch_is_blocked_during_walk(self):
        self.prepare_walk()
        with self.assertRaises(ValueError):
            self.store.touch(self.identity, self.state, 0)
        for seq in range(320):
            t = seq / 80
            xy = ([.5 + 3*t, .5] if t < 1 else [3.5, .5 + 2*(t-1)] if t < 2
                  else [3.5-3*(t-2), 2.5] if t < 3 else [.5, 2.5-2*(t-3)])
            self.now += .1
            self.store.ingest(self.identity, self.walk_frame(xy, seq))
        state = self.store.read(self.identity)
        self.assertGreater(state['walk']['lengthM'], 7)
        restarted = setup.SetupStore(self.holder.name, lambda: self.now)
        self.assertEqual(restarted.read(self.identity)['phase'], 'walk_ready')
        old_id = state['walk']['id']
        self.store.stop_walk(self.identity, state)
        self.assertEqual(state['phase'], 'walk_ready')
        self.store.start_walk(self.identity, state)
        self.assertNotEqual(state['walk']['id'], old_id)
        self.assertTrue((self.store.folder(self.identity) / 'walks' / old_id / 'trail.csv').is_file())

    def test_missing_files_block_job_and_restart_recovers_processing(self):
        self.state.update(phase='walk_ready', walk={'id': 'walk'})
        with self.assertRaisesRegex(ValueError, 'Pointcloud.ply'):
            setup_jobs.start(self.store, self.identity, self.state, lambda device: self.identity)
        self.state.update(phase='processing', jobId='lost-worker')
        self.store.save(self.identity, self.state)
        self.assertEqual(self.store.read(self.identity)['phase'], 'walk_ready')

    def test_background_job_snapshots_inputs_and_persists_review_result(self):
        self.prepare_walk()
        state = self.store.read(self.identity)
        self.store.stop_walk(self.identity, state)
        state.update(phase='walk_ready')
        self.store.save(self.identity, state)
        inputs = self.store.folder(self.identity) / 'inputs'
        (inputs / 'Pointcloud.ply').write_bytes(b'test pointcloud')
        (inputs / 'confirmed-floor.json').write_text('{}')
        asset = Path(__file__).resolve().parents[1] / 'petsense/uwb/assets/reconstruction-room-v1'

        def fake_worker(job):
            self.assertEqual((job / 'Pointcloud.ply').read_bytes(), b'test pointcloud')
            self.assertTrue((job / 'trail.csv').is_file())
            shutil.copytree(asset, job / 'bundle-grid')
            (job / 'result.json').write_text(json.dumps({'recommended': 'grid', 'message': 'review',
                'candidates': [{'id': 'grid', 'eligible': True, 'trail': []}]}))

        def immediate_thread(**kwargs):
            return SimpleNamespace(start=kwargs['target'])

        with patch.object(setup_jobs, 'run_worker', fake_worker), patch.object(setup_jobs.threading, 'Thread', immediate_thread):
            setup_jobs.start(self.store, self.identity, state, lambda device: self.identity)
        ready = self.store.read(self.identity)
        self.assertEqual(ready['phase'], 'alignment_review')
        self.assertEqual(ready['alignment']['recommended'], 'grid')
        self.assertFalse(self.store.jobs)

    def seed_device(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        owner = seed_user('workflow-owner')
        with db.session_scope() as session:
            session.add(Device(device_id='collar', owner_id=owner, name='', claimed_at=db.utc_now()))
        return owner, setup.device_identity('collar')

    def test_publish_validates_job_quality_owner_and_live_transform(self):
        owner, identity = self.seed_device()
        state = self.store.create(identity, 0)
        bundle = self.store.folder(identity) / 'jobs/job1/bundle-mirror'
        asset = Path(__file__).resolve().parents[1] / 'petsense/uwb/assets/reconstruction-room-v1'
        shutil.copytree(asset, bundle)
        manifest = json.loads((bundle / 'map.json').read_text())
        manifest.update(anchors=[{'id': i, 'uwb': xy} for i, xy in enumerate([[0, 0], [4, 0], [0, 3]])],
                        polygon=[[-1, -1], [10, -1], [10, 10], [-1, 10]],
                        uwbTransform={'scale': 1, 'matrix': [[-1, 0], [0, 1]], 'translation': [5, 2]})
        (bundle / 'map.json').write_text(json.dumps(manifest))
        state.update(phase='alignment_review', jobId='job1',
                     alignment={'candidates': [{'id': 'mirror', 'eligible': False}]})
        live = PositionService(Registry(Path(self.holder.name) / 'maps/bindings.json'))
        live.registry.path.parent.mkdir()
        live.registry.path.write_text(json.dumps({'devices': {'unrelated': {'directory': 'untouched'}}}))
        with patch.object(setup_jobs, 'service', live):
            for selected_job in ['old-job', 'job1']:
                with self.assertRaises(ValueError):
                    setup_jobs.publish(self.store, identity, state, 'mirror', selected_job, True)
            state['alignment']['candidates'][0]['eligible'] = True
            with self.assertRaises(ValueError):
                setup_jobs.publish(self.store, identity, state, 'mirror', 'job1')
            setup_jobs.publish(self.store, identity, state, 'mirror', 'job1', True)
        self.assertEqual(state['phase'], 'published')
        self.assertIn('unrelated', json.loads(live.registry.path.read_text())['devices'])
        self.assertIsNone(live.registry.for_device('collar', (owner + 1, identity[2])))
        self.assertIsNotNone(live.map('collar'))
        frame = self.walk_frame([1, 1], 1)
        live.enrich(frame, 'collar')
        self.assertAlmostEqual(frame['uwbPosition']['position']['x'], 4)
        self.assertAlmostEqual(frame['uwbPosition']['position']['y'], 3)

    def test_manual_input_requires_ownership_and_rejects_empty_or_unknown_file(self):
        owner, identity = self.seed_device()
        self.store.create(identity, 0)
        app = Flask(__name__)
        app.register_blueprint(setup.bp)
        client = app.test_client()
        headers = {'X-Petsense-Session': make_session_token(owner)}
        with patch.object(setup, 'store', self.store):
            path = '/api/app/uwb/setup/input/floor?deviceId=collar'
            self.assertEqual(client.put(path, data=b'{"scale_prior":1}', headers=headers).status_code, 200)
            self.assertEqual(client.put(path, data=b'', headers=headers).status_code, 400)
            self.assertEqual(client.put(path, data=b'x').status_code, 401)
            self.assertEqual(client.put(path.replace('floor', 'unknown'), data=b'x', headers=headers).status_code, 400)
            self.assertEqual(client.put(path.replace('collar', 'other'), data=b'x', headers=headers).status_code, 403)
