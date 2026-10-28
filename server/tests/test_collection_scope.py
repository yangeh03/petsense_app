"""App users may collect only their device and access only their own sessions."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from flask import Flask
from helpers import fresh_db
from test_devices import seed_user, make_session_token
from petsense import db
from petsense.collection.api import CollectionStore
from petsense.db.models import CollectionFrame, CollectionVideo, Device


class CollectionScopeTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.sent = []
        self.store = CollectionStore(holder.name, 'legacy-admin', lambda d, c: self.sent.append((d, c)) or True)
        app = Flask(__name__)
        app.register_blueprint(self.store.blueprint())
        self.client = app.test_client()
        self.a, self.b = seed_user('collector-a'), seed_user('collector-b')
        self.ha = {'X-Petsense-Session': make_session_token(self.a)}
        self.hb = {'X-Petsense-Session': make_session_token(self.b)}
        with db.session_scope() as s:
            s.add(Device(device_id='collar-a', owner_id=self.a, name='', claimed_at=db.utc_now()))
        self.base = '/api/collection/v1'
        reply = self.client.post(self.base + '/sessions', headers=self.ha,
                                 json={'id': 'session-a', 'device': 'collar-a'})
        self.assertEqual(reply.status_code, 201)

    def test_session_and_device_scope(self):
        self.assertEqual(self.client.get(self.base + '/time').status_code, 401)
        self.assertEqual(self.client.get(self.base + '/time', headers=self.ha).status_code, 200)
        self.assertEqual(self.client.get(self.base + '/device/collar-a', headers=self.hb).status_code, 404)
        self.assertEqual(self.client.post(self.base + '/sessions', headers=self.hb,
                         json={'id': 'session-a', 'device': 'collar-a'}).status_code, 404)
        self.assertEqual(self.client.get(self.base + '/sessions/session-a/quality', headers=self.hb).status_code, 404)
        self.assertEqual(self.client.post(self.base + '/sessions/session-a/control', headers=self.hb,
                         json={'action': 'stop'}).status_code, 404)
        self.assertEqual(self.sent, [])

    def test_start_checks_live_storage_and_preserves_auth(self):
        url = self.base + '/sessions/session-a/control'
        frame = dict(utc_us=time.time_ns() // 1000, clock_age_us=1000, sd_ready=True,
                     sd_free_bytes=2000000, capture_active=False, storage_draining=False)
        self.store.latest['collar-a'] = dict(received_us=time.time_ns() // 1000, frame=frame)
        self.assertEqual(self.client.post(url, json={'action': 'start'}).status_code, 401)
        for patch in [dict(sd_ready=False), dict(sd_free_bytes=0), dict(disk_errors=1),
                      dict(storage_draining=True), dict(capture_active=True, session_id='different')]:
            self.store.latest['collar-a']['frame'] = dict(frame, **patch)
            self.assertEqual(self.client.post(url, headers=self.ha, json={'action': 'start'}).status_code, 409)
        self.assertEqual(self.sent, [])
        self.store.latest['collar-a']['frame'] = frame
        response = self.client.post(url, headers=self.ha, json={'action': 'start'})
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json['device_acknowledged'])
        self.assertEqual(self.sent[-1], ('collar-a', {'type': 'collection_start', 'session': 'session-a'}))

    def test_all_video_endpoints_are_scoped(self):
        base = self.base + '/sessions/session-a/videos'
        self.assertEqual(self.client.post(base, headers=self.hb, json={}).status_code, 404)
        for method, suffix in [('GET', '/v/annotation'), ('GET', '/v/upload'),
                               ('PUT', '/v/upload?offset=0'), ('POST', '/v/finish'),
                               ('POST', '/v/alignment')]:
            result = self.client.open(base + suffix, method=method, headers=self.hb, json={})
            self.assertEqual(result.status_code, 404)

    def test_transfer_does_not_transfer_history_or_allow_old_owner_control(self):
        with db.session_scope() as s:
            s.get(Device, 'collar-a').owner_id = self.b
        path = self.base + '/sessions/session-a'
        self.assertEqual(self.client.get(path + '/quality', headers=self.ha).status_code, 200)
        self.assertEqual(self.client.get(path + '/quality', headers=self.hb).status_code, 404)
        self.assertEqual(self.client.post(path + '/control', headers=self.ha,
                         json={'action': 'stop'}).status_code, 404)

    def test_manual_alignment_and_device_filter(self):
        folder = self.store.root / 'session-a' / 'v'
        folder.mkdir()
        now = time.time_ns() // 1000
        frame = {'marker_id': 'shake-a', 'utc_us': now, 'clock_uncertainty_us': 2000}
        with db.session_scope() as s:
            s.add(CollectionVideo(id='v', session='session-a', complete=1,
                                  document=json.dumps({'duration_ms': 5000, 'calibration_marker': 'shake-a',
                                      'labels': [{'event_id': 'calibration', 'start_ms': 0, 'end_ms': 4000}]})))
            for device, seq in [('collar-a', 1), ('foreign', 2)]:
                s.add(CollectionFrame(device=device, boot='boot', seq=seq, session='session-a',
                                      mono_us=seq*100000, utc_us=now, received_us=now,
                                      document=json.dumps(frame)))
        path = self.base + '/sessions/session-a'
        quality = self.client.get(path + '/quality', headers=self.ha).json
        self.assertEqual(quality['count'], 1)
        reply = self.client.post(path + '/videos/v/alignment', headers=self.ha,
                                 json={'boot': 'boot', 'seq': 1, 'video_ms': 1500})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json['alignment']['aligned_start_utc_ms'], now / 1000 - 1500)
        self.assertEqual(reply.json['aligned_labels'][0]['end_utc_ms'], now / 1000 + 2500)
        self.assertTrue((folder / 'annotation.json').exists())
        self.assertEqual(self.client.post(path + '/videos/v/alignment', headers=self.ha,
                         json={'boot': 'boot', 'seq': 2, 'video_ms': 1500}).status_code, 400)
        self.assertEqual(self.client.post(path + '/videos/v/alignment', headers=self.ha,
                         json={'boot': 'boot', 'seq': 1, 'video_ms': float('nan')}).status_code, 400)
