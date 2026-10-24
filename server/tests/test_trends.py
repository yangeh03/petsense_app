import sys
import unittest
import unittest.mock
import hashlib
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import config, db
from petsense.db.models import AuthSession, Device, Reading, User
from petsense.trends import routes as trends_routes
from helpers import fresh_db


def make_trends_client():
    app = Flask(__name__)
    app.register_blueprint(trends_routes.bp)
    return app.test_client()


def seed_user_with_device(username, device_id):
    with db.session_scope() as s:
        user = User(username=username, display_name=username, password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        s.add(Device(device_id=device_id, owner_id=user.id, name='', claimed_at=db.utc_now()))
        token = secrets.token_urlsafe(16)
        s.add(AuthSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires_at=db.utc_now() + timedelta(days=1),
        ))
        return token


class TrendsTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.client = make_trends_client()

    def seed_readings(self, device_id, count=3, topic=None):
        payload = '{"health":{"valid":true,"heartRate":100,"spo2":99,"bodyTemp":38.2}}'
        with db.session_scope() as s:
            for i in range(count):
                s.add(Reading(topic=topic or f'petsense/sensor/{device_id}', payload=payload,
                              received_at=db.utc_now() - timedelta(minutes=i),
                              device_id=device_id))

    def test_day_period_scoped_to_my_device_and_requires_auth(self):
        token = seed_user_with_device('alice', 'dev-a')
        self.assertEqual(self.client.get('/api/app/trends?period=day').status_code, 401)
        self.seed_readings('dev-a')
        self.seed_readings('dev-b', topic='petsense/sensor/dev-b')
        buckets = self.client.get('/api/app/trends?period=day',
                                  headers={'X-Petsense-Session': token}).get_json()['buckets']
        self.assertTrue(buckets)
        self.assertEqual(buckets[-1]['samples'], 9)  # 3 帧 × hr/spo2/temp，不含 dev-b

    def test_day_period_empty_without_device(self):
        token = seed_user_with_device('alice', 'dev-a')
        self.assertEqual(self.client.get('/api/app/trends?period=day',
                         headers={'X-Petsense-Session': token}).get_json()['buckets'], [])

    def test_experiment_aggregation_filtered_by_manifest_device(self):
        token = seed_user_with_device('alice', 'dev-a')
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        for exp, device in (('exp-mine', 'dev-a'), ('exp-other', 'dev-b')):
            folder = Path(holder.name) / exp
            folder.mkdir()
            (folder / 'manifest.json').write_text(
                '{"device_id": "%s", "device_topic": "petsense/sensor/%s"}' % (device, device))
            (folder / 'health.csv').write_text(
                'received_at,valid,heart_rate_bpm,spo2_pct,body_temp_c\n'
                '2026-10-24T10:00:00.000Z,true,90,98,38.0\n')
        with unittest.mock.patch.object(config, 'CLOUD_DATA_DIR', holder.name):
            buckets = self.client.get('/api/app/trends?period=all',
                                      headers={'X-Petsense-Session': token}).get_json()['buckets']
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]['samples'], 3)


if __name__ == '__main__':
    unittest.main()
