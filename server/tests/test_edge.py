import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_devices import seed_user, make_session_token
from petsense import db
from petsense.db.models import Device, Reading
from petsense.telemetry import edge, mqtt_ingest


class EdgeTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        app = Flask(__name__)
        app.register_blueprint(edge.bp)
        self.client = app.test_client()
        self.owner = seed_user('edge-owner')
        self.other = seed_user('edge-other')
        self.headers = {'X-Petsense-Session': make_session_token(self.owner)}
        self.other_headers = {'X-Petsense-Session': make_session_token(self.other)}
        with db.session_scope() as s:
            s.add(Device(device_id='test-edge', owner_id=self.owner, name='', claimed_at=db.utc_now()))
        self.payload = {'source': 'esp32_mlp', 'deviceId': 'test-edge', 'ts_ms': 1000,
                        'action': {'label': 'standing', 'confidence': .9},
                        'sound': {'label': 'whine', 'confidence': .7}, 'health': {}}

    def ingest(self):
        msg = SimpleNamespace(topic=edge.TOPIC, payload=json.dumps(self.payload).encode())
        with patch.object(mqtt_ingest, 'broadcast_packet') as broadcast, \
                patch.object(mqtt_ingest.actions_service, 'enrich_frame_with_action') as enrich:
            mqtt_ingest.on_message(None, None, msg)
            enrich.assert_not_called()
            self.assertEqual(broadcast.call_args.args[1], 'test-edge')

    def test_owner_scope_and_roundtrip(self):
        self.ingest()
        url = '/api/app/edge/latest'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertIsNone(self.client.get(url, headers=self.other_headers).json['payload'])
        result = self.client.get(url, headers=self.headers)
        self.assertEqual(result.json['payload'], self.payload)
        self.assertTrue(result.json['online'])
        self.assertEqual(result.headers['Cache-Control'], 'no-store')

    def test_validation_and_reserved_topic(self):
        self.assertIsNone(mqtt_ingest.sensor_device(edge.TOPIC))
        self.payload['sound']['confidence'] = float('nan')
        with self.assertRaises(ValueError):
            edge.validate(self.payload)
        with patch.object(mqtt_ingest, 'store') as store:
            mqtt_ingest.on_message(None, None, SimpleNamespace(topic=edge.TOPIC, payload=b'{}'))
            store.assert_not_called()

    def test_static_page_has_no_device_data(self):
        result = self.client.get('/edge')
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'X-Petsense-Session', result.data)
        result.close()
