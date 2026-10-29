import json
import unittest
from petsense.telemetry import gps
from petsense.telemetry.mqtt_ingest import compact_sample_to_payload
from petsense.telemetry.realtime import public_packet


def frame():
    return dict(ts_ms=12000, gps=dict(source='A7670E_GNSS', crs='WGS84', enabled=True,
                valid=True, status='fixed', receivedMs=10000, sequence=1,
                latitude=31.2223881, longitude=121.3539011,
                utc='2026-10-29T08:00:00.000Z', speedMps=5.14, altitudeM=44.1))


class GpsTests(unittest.TestCase):
    def test_fix_and_zero_coordinates(self):
        value = frame()
        value['gps'].update(latitude=0, longitude=0)
        gps.normalize(value)
        self.assertTrue(value['gps']['valid'])
        self.assertEqual(value['gps']['remainingMs'], 13000)
        self.assertEqual(value['gps']['latitude'], 0)

    def test_stale_includes_batch_age_and_wrap(self):
        value = frame()
        gps.normalize(value, 14000)
        self.assertEqual(value['gps']['status'], 'stale')
        self.assertIsNone(value['gps']['latitude'])
        value = frame()
        value['ts_ms'] = 1000
        value['gps']['receivedMs'] = 0xffffffff - 999
        gps.normalize(value)
        self.assertEqual(value['gps']['ageMs'], 2000)
        self.assertTrue(value['gps']['valid'])

    def test_invalid_gps_does_not_poison_other_telemetry(self):
        for change in (dict(latitude=float('nan')), dict(longitude=181), dict(latitude=True),
                       dict(utc='invalid'), dict(receivedMs=12001), dict(valid=False),
                       dict(enabled=False), dict(source='unknown')):
            value = frame()
            value['health'] = {'valid': True, 'heartRate': 80}
            value['gps'].update(change)
            gps.normalize(value)
            self.assertFalse(value['gps']['valid'])
            self.assertIsNone(value['gps']['latitude'])
            self.assertEqual(value['health']['heartRate'], 80)
            json.dumps(value, allow_nan=False)

    def test_compact_frame_preserves_gps_and_old_frames_still_work(self):
        value = compact_sample_to_payload({'deviceId': 'test'}, {'t': 12000, 'gps': frame()['gps']})
        gps.normalize(value)
        self.assertTrue(value['gps']['valid'])
        self.assertNotIn('gps', compact_sample_to_payload({'deviceId': 'test'}, {'t': 100}))

    def test_public_sse_omits_gps_for_text_and_object_payloads(self):
        for encode in (lambda x: x, json.dumps):
            packet = json.dumps({'topic': 'petsense/sensor/test', 'payload': encode(frame())})
            result = json.loads(public_packet(packet))['payload']
            result = json.loads(result) if isinstance(result, str) else result
            self.assertNotIn('gps', result)
            self.assertEqual(result['ts_ms'], 12000)
