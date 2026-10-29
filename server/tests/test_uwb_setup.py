import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_devices import seed_user, make_session_token
from petsense import db
from petsense.db.models import Device
from petsense.uwb import setup


class UwbSetupTests(unittest.TestCase):
    def setUp(self):
        self.holder = tempfile.TemporaryDirectory()
        self.addCleanup(self.holder.cleanup)
        self.now = 100
        self.store = setup.SetupStore(self.holder.name, lambda: self.now)
        self.identity = ('collar', 1, 'claim')
        self.state = self.store.create(self.identity, 0)

    def frame(self, anchor, sequence=1):
        ranges = [[0, 4000, 3000], [4000, 0, 5000], [3000, 5000, 0]][anchor]
        return {'deviceId': 'collar', 'ts_ms': 1000,
                'uwb': {'valid': True, 'tagId': 0, 'timer': sequence, 'sequence': sequence,
                        'receivedMs': 1000, 'anchorMask': 7 ^ (1 << anchor),
                        'distanceMm': ranges}}

    def capture(self, anchor):
        self.store.touch(self.identity, self.state, anchor)
        for seq in range(4):
            self.store.ingest(self.identity, self.frame(anchor, seq))
        self.now += 7
        self.state = self.store.read(self.identity)

    def test_three_touches_recover_known_geometry_and_file_slots(self):
        for anchor in range(3):
            self.capture(anchor)
        self.assertEqual(self.state['phase'], 'anchors_ready')
        for actual, expected in zip(self.state['anchors'], [[0, 0], [4, 0], [0, 3]]):
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b)
        self.assertTrue(Path(self.state['files']['pointcloud']).parent.is_dir())
        self.assertEqual(len((self.store.folder(self.identity) / 'touch.ndjson').read_text().splitlines()), 12)

    def test_duplicate_stale_wrong_tag_missing_anchor_do_not_count(self):
        self.store.touch(self.identity, self.state, 0)
        frame = self.frame(0)
        for _ in range(10):
            self.store.ingest(self.identity, frame)
        frame = self.frame(0, 2)
        frame['uwb']['tagId'] = 1
        self.store.ingest(self.identity, frame)
        frame = self.frame(0, 3)
        frame['ts_ms'] = 4000
        self.store.ingest(self.identity, frame)
        frame = self.frame(0, 4)
        frame['uwb']['anchorMask'] = 2
        self.store.ingest(self.identity, frame)
        self.now += 7
        state = self.store.read(self.identity)
        self.assertIsNone(state['rows'][0])
        self.assertIn('不足', state['message'])

    def test_restart_marks_capture_interrupted_and_ownership_isolated(self):
        self.store.touch(self.identity, self.state, 0)
        restarted = setup.SetupStore(self.holder.name, lambda: self.now)
        self.assertEqual(restarted.read(self.identity)['phase'], 'touch_ready')
        self.assertIsNone(restarted.read(('collar', 2, 'new-claim')))

    def test_retry_invalidates_later_rows_and_parallel_capture_rejected(self):
        for anchor in range(3):
            self.capture(anchor)
        self.store.touch(self.identity, self.state, 0)
        self.assertEqual(self.state['rows'], [None] * 3)
        self.assertIsNone(self.state['anchors'])
        with self.assertRaises(ValueError):
            self.store.touch(self.identity, self.state, 1)

    def test_bad_geometry_rejected(self):
        for rows in [[[0, 1, 2], [1, 0, 1], [2, 1, 0]],
                     [[0, 4, 3], [5, 0, 5], [3, 5, 0]],
                     [[0, math.nan, 3], [4, 0, 5], [3, 5, 0]]]:
            with self.assertRaises(ValueError):
                setup.solve_anchors(rows)

    def test_routes_require_session_and_device_ownership(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        owner = seed_user('setup-owner')
        other = seed_user('setup-other')
        with db.session_scope() as session:
            session.add(Device(device_id='collar', owner_id=owner, name='', claimed_at=db.utc_now()))
        app = Flask(__name__)
        app.register_blueprint(setup.bp)
        client = app.test_client()
        with patch.object(setup, 'store', self.store):
            self.assertEqual(client.get('/api/app/uwb/setup?deviceId=collar').status_code, 401)
            headers = {'X-Petsense-Session': make_session_token(other)}
            self.assertEqual(client.get('/api/app/uwb/setup?deviceId=collar', headers=headers).status_code, 403)
            headers = {'X-Petsense-Session': make_session_token(owner)}
            response = client.post('/api/app/uwb/setup', json={'deviceId': 'collar'}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['session']['tagId'], 0)
            response = client.post('/api/app/uwb/setup/touch', json={'deviceId': 'collar', 'anchor': 0}, headers=headers)
            self.assertEqual(response.json['session']['phase'], 'touching')
            # Exercise the same owner lookup and normalized telemetry entry point as MQTT.
            for seq in range(4):
                setup.ingest(self.frame(0, seq), 'collar')
            self.now += 7
            response = client.get('/api/app/uwb/setup?deviceId=collar', headers=headers)
            self.assertEqual(response.json['session']['rows'][0], [0, 4, 3])
            response = client.post('/api/app/uwb/setup/cancel', json={'deviceId': 'collar'}, headers=headers)
            self.assertEqual(response.status_code, 400)
