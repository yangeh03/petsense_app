import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import func, select

from petsense import config, db, stats
from petsense.db.models import Device, Pet, Reading, User
from petsense.telemetry import mqtt_ingest, realtime
from helpers import fresh_db


def make_realtime_app():
    app = Flask(__name__)
    realtime.sock.init_app(app)
    app.register_blueprint(realtime.bp)
    return app.test_client()


def seed_user_with_device(username, device_id):
    with db.session_scope() as s:
        user = User(username=username, display_name=username, password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        s.add(Pet(user_id=user.id, name='Pet' + username, species='dog', breed='',
                  avatar_emoji='🐾', device_id=device_id, created_at=db.utc_now()))
        s.add(Device(device_id=device_id, owner_id=user.id, name='',
                     claimed_at=db.utc_now()))
        return user.id


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)

    def test_store_records_device_id_for_multiple_devices(self):
        mqtt_ingest.store('petsense/sensor/dev-a', '{}', 'dev-a')
        mqtt_ingest.store('petsense/sensor/dev-b', '{}', 'dev-b')
        mqtt_ingest.store('petsense/sensor/audio_file', '{}')
        with db.session_scope() as s:
            rows = {row.device_id for row in s.execute(select(Reading.device_id)).fetchall()}
        self.assertEqual(rows, {'dev-a', 'dev-b', None})

    def test_sensor_device_extraction(self):
        self.assertEqual(mqtt_ingest.sensor_device('petsense/sensor/esp32-001'), 'esp32-001')
        self.assertIsNone(mqtt_ingest.sensor_device('petsense/sensor/audio_file'))
        self.assertIsNone(mqtt_ingest.sensor_device('petsense/sensor/a/b'))
        self.assertIsNone(mqtt_ingest.sensor_device('petsense/device/x/command'))

    def test_daily_stats_follows_device_ownership(self):
        seed_user_with_device('alice', 'dev-a')
        frame = {'ts_ms': 1000, 'imu': {'ok': True, 'accel': [0, 0, 1.2]},
                 'health': {'valid': True, 'heartRate': 100}}
        stats.update_daily_stats('dev-a', frame, db.utc_now())
        stats.update_daily_stats('dev-a', {**frame, 'ts_ms': 3000}, db.utc_now())
        stats.update_daily_stats('dev-a', {**frame, 'ts_ms': 5000}, db.utc_now())
        stats.update_daily_stats('dev-unclaimed', frame, db.utc_now())  # 未认领：跳过
        from petsense.db.models import DailyStat
        with db.session_scope() as s:
            rows = s.execute(select(DailyStat.pet_id, DailyStat.samples)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].samples, 2)  # 首帧仅建立时间基准，第 2/3 帧累计

    def test_uwb_remembered_per_device(self):
        frame = {'deviceId': 'dev-a', 'ts_ms': 1, 'uwb': {'valid': True}}
        stats.remember_uwb(frame, '2026-10-25T00:00:00.000Z', 'dev-a')
        stats.remember_uwb({'uwb': {'valid': True}}, '2026-10-25T00:01:00.000Z', 'dev-b')
        self.assertEqual(stats.last_uwb_frames['dev-a']['payload']['deviceId'], 'dev-a')
        self.assertIn('dev-b', stats.last_uwb_frames)
        self.assertIsNotNone(stats.last_uwb_frame)

    def test_store_returns_aware_and_trims_by_watermark(self):
        moment = mqtt_ingest.store('petsense/sensor/esp32-001', '{"a":1}')
        self.assertIsNotNone(moment.tzinfo)
        with patch.object(config, 'MAX_HISTORY', 10):
            mqtt_ingest._trim_counter = 499  # 下一次插入触发裁剪
            mqtt_ingest.store('t', '{"i":0}')
        with db.session_scope() as s:
            count = s.execute(select(func.count()).select_from(Reading)).scalar_one()
        self.assertLessEqual(count, 10)

    def test_latest_full_shape_unchanged(self):
        mqtt_ingest.store('petsense/sensor/x', '{"heartRate": 88}')
        payload = make_realtime_app().get('/api/latest-full').get_json()
        self.assertEqual(payload['count'], 1)
        row = payload['data'][0]
        self.assertEqual(row['topic'], 'petsense/sensor/x')
        self.assertEqual(row['payload'], {'heartRate': 88})
        self.assertRegex(row['received_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')

    def test_stats_endpoint_counts(self):
        mqtt_ingest.store('t', '{}')
        stats = make_realtime_app().get('/api/stats').get_json()
        self.assertEqual(stats['total'], 1)
        self.assertRegex(stats['latest'], r'Z$')


if __name__ == '__main__':
    unittest.main()
