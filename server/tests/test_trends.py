import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import Reading
from petsense.trends import routes as trends_routes
from helpers import fresh_db


def make_trends_client():
    app = Flask(__name__)
    app.register_blueprint(trends_routes.bp)
    return app.test_client()


class TrendsTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)

    def test_day_period_buckets_include_recent_valid_frames(self):
        payload = '{"health":{"valid":true,"heartRate":100,"spo2":99,"bodyTemp":38.2}}'
        with db.session_scope() as s:
            for i in range(3):
                s.add(Reading(topic='petsense/sensor/esp32-001', payload=payload,
                              received_at=db.utc_now() - timedelta(minutes=i)))
        buckets = make_trends_client().get('/api/app/trends?period=day').get_json()['buckets']
        self.assertTrue(buckets)
        self.assertEqual(buckets[-1]['samples'], 9)  # 3 帧 × hr/spo2/temp

    def test_day_period_empty(self):
        self.assertEqual(make_trends_client().get('/api/app/trends?period=day').get_json()['buckets'], [])

    def test_day_period_excludes_stale_frames(self):
        payload = '{"health":{"valid":true,"heartRate":100,"spo2":99,"bodyTemp":38.2}}'
        with db.session_scope() as s:
            s.add(Reading(topic='petsense/sensor/esp32-001', payload=payload,
                          received_at=db.utc_now() - timedelta(hours=25)))
        self.assertEqual(make_trends_client().get('/api/app/trends?period=day').get_json()['buckets'], [])


if __name__ == '__main__':
    unittest.main()
