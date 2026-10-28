"""C++ parser output must survive MQTT expansion, ownership and aggregation."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpers import fresh_db
from test_ws_scope import FakeWS, seed_user_with_device
from petsense import db, stats
from petsense.db.models import Reading, TelemetryLatest
from petsense.telemetry import mqtt_ingest, realtime

class UwbFirmwareContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Also checked against the real C++ parser by firmware/tests. Keep this
        # fixture in server/ so server-only deployment archives can run tests.
        cls.wire = json.loads((Path(__file__).parent / 'fixtures/uwb_wire.json').read_text())

    def setUp(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        # Never connect MQTT or use live accounts/devices.
        seed_user_with_device('uwb-owner', self.wire['deviceId'])
        seed_user_with_device('uwb-other', 'other-collar')

    def send_sample(self, fresh):
        u = [*self.wire['u']]
        u[0] = fresh
        msg = SimpleNamespace(topic=self.wire['topic'], payload=json.dumps({
            'deviceId': self.wire['deviceId'],
            'samples': [{'t': 200, 'u': u}],
        }).encode())
        owner, other = FakeWS(), FakeWS()
        # Patch the registry in place for this test's broadcast only.
        with patch.dict(realtime.ws_clients, {
                owner: (1, frozenset({self.wire['deviceId']})),
                other: (2, frozenset({'other-collar'})),
        }, clear=True), patch.dict(stats.last_uwb_frames, {}, clear=True), \
                patch.object(mqtt_ingest.actions_service, 'enrich_frame_with_action'), \
                patch.object(mqtt_ingest.recorder, 'append_cloud_experiment_frame'):
            mqtt_ingest.on_message(None, None, msg)
            self.assertEqual(other.sent, [])
            self.assertEqual(len(owner.sent), 1)
            packet = json.loads(owner.sent[0])
            frame = json.loads(packet['payload'])
            self.assertEqual(frame['uwb']['valid'], fresh)
            self.assertEqual(frame['uwb']['tagId'], 7)
            self.assertEqual(frame['uwb']['distanceMm'][1], 2000)
            self.assertIn(self.wire['deviceId'], stats.last_uwb_frames)
        with db.session_scope() as session:
            self.assertEqual(session.query(Reading).one().device_id, self.wire['deviceId'])
            latest = session.get(TelemetryLatest, self.wire['deviceId'])
            self.assertIsNotNone(latest)  # ID mismatch would silently bypass this aggregation.
            self.assertEqual(json.loads(latest.payload)['uwb']['valid'], fresh)

    def test_fresh_u_reaches_owner_and_durable_latest(self):
        self.send_sample(True)

    def test_expired_u_is_preserved_as_invalid(self):
        self.send_sample(False)
