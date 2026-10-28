import copy
from datetime import timedelta
import json
import math
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_devices import seed_user, make_session_token
from petsense import db
from petsense.db.models import Device
from petsense.uwb.maps import Registry, MapBundle
from petsense.uwb.service import PositionService
from petsense.uwb import routes
from petsense.uwb import service as service_module
from petsense.telemetry import mqtt_ingest, realtime
from test_ws_scope import FakeWS

ASSET = Path(__file__).resolve().parents[1] / 'petsense/uwb/assets/reconstruction-room-v1'


class UwbPositioningTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        shutil.copytree(ASSET, self.root / 'room')
        self.manifest = self.root / 'room/map.json'
        self.data = json.loads(self.manifest.read_text())
        self.data.update(anchors=[{'id': i, 'uwb': p} for i, p in enumerate([[0, 0], [4, 0], [0, 4]])],
                         polygon=[[-1, -1], [6, -1], [6, 6], [-1, 6]],
                         uwbTransform={'scale': 1, 'matrix': [[1, 0], [0, 1]], 'translation': [0, 0]})
        self.manifest.write_text(json.dumps(self.data))
        self.binding = self.root / 'bindings.json'
        self.binding.write_text(json.dumps({'devices': {'collar': {'directory': 'room', 'tagId': 7}}}))
        self.service = PositionService(Registry(self.binding))
        self.owner = seed_user('map-owner')
        self.other = seed_user('map-other')
        with db.session_scope() as session:
            session.add(Device(device_id='collar', owner_id=self.owner, name='', claimed_at=db.utc_now()))

    def frame(self, sample=1000, timer=1000, sequence=1):
        return {'deviceId': 'collar', 'ts_ms': sample,
                'uwb': {'valid': True, 'tagId': 7, 'timer': timer, 'sequence': sequence,
                        'receivedMs': sample, 'anchorMask': 7,
                        'distanceMm': [math.sqrt(2)*1000, math.sqrt(10)*1000, math.sqrt(10)*1000]}}

    def test_known_point_and_transform(self):
        f = self.frame()
        self.service.enrich(f, 'collar')
        p = f['uwbPosition']
        self.assertEqual(p['status'], 'ok')
        self.assertAlmostEqual(p['position']['x'], 1)
        self.assertAlmostEqual(p['position']['y'], 1)
        self.assertTrue(p['quality']['insideMap'])

    def test_duplicates_do_not_refresh_deadline_and_read_expires_without_new_data(self):
        with patch('petsense.uwb.service.time.monotonic', return_value=10):
            self.service.enrich(self.frame(), 'collar')
        with patch('petsense.uwb.service.time.monotonic', return_value=11):
            duplicate = self.frame(sample=1100)
            self.service.enrich(duplicate, 'collar')
            self.assertEqual(duplicate['uwbPosition']['remainingMs'], 1000)
        with patch('petsense.uwb.service.time.monotonic', return_value=13):
            p = self.service.latest('collar')
            self.assertFalse(p['fresh'])
            self.assertEqual(p['status'], 'stale')

    def test_uptime_age_and_batch_age_shorten_freshness(self):
        f = self.frame()
        f['uwb']['receivedMs'] = 500
        self.service.enrich(f, 'collar', batch_age_ms=500)
        self.assertLessEqual(f['uwbPosition']['remainingMs'], 1000)
        invalid = self.frame(timer=2000)
        invalid['uwb']['valid'] = False
        self.service.enrich(invalid, 'collar')
        self.assertFalse(invalid['uwbPosition']['fresh'])

    def test_mask_nonfinite_wrong_tag_and_untrusted_position_are_not_used(self):
        for key, value in [('anchorMask', 1), ('tagId', 8), ('distanceMm', [float('nan')]*3)]:
            f = self.frame()
            f['uwb'][key] = value
            f['uwbPosition'] = {'position': {'x': 999, 'y': 999}}
            self.service.enrich(f, 'collar')
            self.assertNotIn('uwbPosition', f)

    def test_out_of_order_does_not_move_position(self):
        self.service.enrich(self.frame(), 'collar')
        f = self.frame(sample=1100, timer=999, sequence=0)
        self.service.enrich(f, 'collar')
        self.assertNotIn('uwbPosition', f)
        self.assertEqual(self.service.latest('collar')['sequence'], 1)

    def test_tag_timer_can_restart_after_expiry_without_rebooting_esp32(self):
        with patch('petsense.uwb.service.time.monotonic', return_value=10):
            self.service.enrich(self.frame(timer=90000), 'collar')
        with patch('petsense.uwb.service.time.monotonic', return_value=13):
            f = self.frame(sample=4000, timer=100, sequence=0)
            self.service.enrich(f, 'collar')
            self.assertTrue(f['uwbPosition']['fresh'])
            self.assertEqual(f['uwbPosition']['sequence'], 0)

    def test_mqtt_batch_attaches_server_position_only_to_device_owner(self):
        f = self.frame()
        u = f['uwb']
        ranges = u['distanceMm'] + [0] * 5
        sample = [True, 1000, 1000, 7, 2, 1, 7, False, ranges, ranges, [0]*8]
        msg = SimpleNamespace(topic='petsense/sensor/collar', payload=json.dumps({
            'deviceId': 'collar', 'samples': [{'t': 1000, 'u': sample}]}).encode())
        owner, other = FakeWS(), FakeWS()
        with patch.object(service_module, 'service', self.service), \
                patch.object(mqtt_ingest.actions_service, 'enrich_frame_with_action'), \
                patch.object(mqtt_ingest.recorder, 'append_cloud_experiment_frame'), \
                patch.dict(realtime.ws_clients, {owner: (self.owner, frozenset({'collar'})),
                                                other: (self.other, frozenset({'other'}))}, clear=True):
            mqtt_ingest.on_message(None, None, msg)
        self.assertEqual(other.sent, [])
        frame = json.loads(json.loads(owner.sent[0])['payload'])
        self.assertAlmostEqual(frame['uwbPosition']['position']['x'], 1)
        self.assertEqual(frame['uwb']['tagId'], 7)

    def test_owner_transfer_invalidates_cached_location(self):
        self.service.enrich(self.frame(), 'collar')
        with db.session_scope() as session:
            device = session.get(Device, 'collar')
            device.owner_id = self.other
            device.claimed_at = db.utc_now() + timedelta(seconds=1)
        self.assertIsNone(self.service.latest('collar'))

    def test_precise_coordinates_do_not_enter_legacy_unscoped_sse(self):
        packet = json.dumps({'payload': json.dumps({'uwb': {'valid': True}, 'uwbPosition': {'position': {'x': 1, 'y': 2}}})})
        with patch.object(realtime.fwd, 'broadcast') as sse, patch.dict(realtime.ws_clients, {}, clear=True):
            realtime.broadcast_packet(packet, 'collar')
        public = json.loads(json.loads(sse.call_args.args[0])['payload'])
        self.assertNotIn('uwbPosition', public)
        self.assertIn('uwb', public)

    def test_routes_require_auth_and_ownership_and_default_unbound_is_empty(self):
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        client = app.test_client()
        owner = {'X-Petsense-Session': make_session_token(self.owner)}
        other = {'X-Petsense-Session': make_session_token(self.other)}
        with patch.object(routes, 'service', self.service):
            self.assertEqual(client.get('/api/app/uwb/map').status_code, 401)
            self.assertEqual(client.get('/api/app/uwb/map?deviceId=collar', headers=other).status_code, 403)
            result = client.get('/api/app/uwb/map?deviceId=collar', headers=owner).json
            self.assertTrue(result['map']['background']['imageDataUri'].startswith('data:image/png;base64,'))
            self.assertIsNone(client.get('/api/app/uwb/position', headers=owner).json['position'])
            self.binding.write_text('{"devices": {}}')
            self.assertIsNone(client.get('/api/app/uwb/map', headers=owner).json['map'])

    def test_bad_geometry_and_asset_traversal_are_rejected(self):
        for change in ['scale', 'matrix', 'image']:
            d = copy.deepcopy(self.data)
            if change == 'scale':
                d['uwbTransform']['scale'] = 0
            elif change == 'matrix':
                d['uwbTransform']['matrix'] = [[1, 1], [1, 1]]
            else:
                d['background']['file'] = '../../private.png'
            self.manifest.write_text(json.dumps(d))
            with self.assertRaises(ValueError):
                MapBundle(self.manifest.parent)

    def test_malformed_runtime_configuration_does_not_interrupt_telemetry(self):
        self.binding.write_text('{"devices": []}')
        with patch.object(service_module, 'service', self.service):
            frame = self.frame()
            service_module.enrich(frame, 'collar', db.utc_now())
            self.assertNotIn('uwbPosition', frame)
