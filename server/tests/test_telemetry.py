import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import func, select

from petsense import config, db
from petsense.db.models import Reading
from petsense.telemetry import mqtt_ingest, realtime
from helpers import fresh_db


def make_realtime_app():
    app = Flask(__name__)
    realtime.sock.init_app(app)
    app.register_blueprint(realtime.bp)
    return app.test_client()


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)

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
