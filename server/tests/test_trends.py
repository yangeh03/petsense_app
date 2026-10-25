import hashlib
import json
import secrets
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask
from petsense import config, db
from petsense.db.models import AuthSession, Device, Reading, User
from petsense.trends import routes
from helpers import fresh_db

NOW = datetime(2026, 10, 25, 6, 30, tzinfo=timezone.utc)


def seed_user_with_device(username, device_id):
    with db.session_scope() as s:
        user = User(username=username, display_name=username, password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        s.add(Device(device_id=device_id, owner_id=user.id, name='', claimed_at=db.utc_now()))
        token = secrets.token_urlsafe(16)
        s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                          expires_at=db.utc_now() + timedelta(days=1)))
        return token


class TrendsTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        self.token = seed_user_with_device('alice', 'dev-a')
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.patch = patch.object(config, 'CLOUD_DATA_DIR', self.folder.name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.clock = patch.object(routes, 'utc_now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def get(self, period='day'):
        return self.client.get('/api/app/trends?period=' + period,
                               headers={'X-Petsense-Session': self.token})

    def reading(self, at, hr=100, spo2=99, temp=38.2, device='dev-a', valid=True):
        with db.session_scope() as s:
            s.add(Reading(device_id=device, topic=f'petsense/sensor/{device}', received_at=at,
                          payload=json.dumps({'health': {'valid': valid, 'heartRate': hr,
                                                        'spo2': spo2, 'bodyTemp': temp}})))

    def archive(self, rows, device='dev-a', name='experiment'):
        folder = Path(self.folder.name) / name
        folder.mkdir(exist_ok=True)
        (folder / 'manifest.json').write_text(json.dumps({'device_id': device}))
        path = folder / 'health.csv'
        path.write_text('received_at,valid,heart_rate_bpm,spo2_pct,body_temp_c\n' + rows)
        return path

    def test_auth_and_device_isolation(self):
        self.assertEqual(self.client.get('/api/app/trends').status_code, 401)
        self.reading(NOW, device='dev-b', hr=250)
        self.archive('2026-10-25T06:00:00Z,true,230,98,38\n', device='dev-b')
        self.reading(NOW, hr=80)
        data = self.get().get_json()
        self.assertEqual(data['summary']['heartRate']['count'], 1)
        self.assertEqual(data['summary']['heartRate']['avg'], 80)

    def test_weighted_summary_uses_raw_extrema_and_independent_counts(self):
        self.reading(NOW - timedelta(hours=1), hr=60, spo2=None, temp=37)
        self.reading(NOW - timedelta(minutes=5), hr=100, spo2=98, temp=40)
        self.reading(NOW, hr=140, spo2=96, temp=None)
        data = self.get().get_json()
        self.assertEqual(data['schemaVersion'], 2)
        self.assertEqual(data['summary']['heartRate'], {'count': 3, 'avg': 100, 'min': 60, 'max': 140})
        self.assertEqual(data['summary']['spo2'], {'count': 2, 'avg': 97, 'min': 96, 'max': 98})
        self.assertEqual(data['summary']['bodyTemp'], {'count': 2, 'avg': 38.5, 'min': 37, 'max': 40})
        self.assertEqual(sum(b['samples'] for b in data['buckets']), 3)

    def test_rolling_window_preserves_empty_hours_and_boundaries(self):
        self.reading(NOW - timedelta(hours=24), hr=80)
        self.reading(NOW - timedelta(hours=24, microseconds=1), hr=200)
        self.reading(NOW + timedelta(microseconds=1), hr=220)
        data = self.get().get_json()
        self.assertEqual(len(data['buckets']), 25)
        self.assertEqual(data['window']['start'], '2026-10-24T06:30:00Z')
        self.assertEqual(data['buckets'][0]['start'], '2026-10-24T06:30:00Z')
        self.assertEqual(data['buckets'][-1]['end'], '2026-10-25T06:30:00Z')
        self.assertEqual(data['coverage']['heartRate'], {'recordedBuckets': 1, 'totalBuckets': 25})
        self.assertEqual(data['summary']['heartRate']['count'], 1)
        self.assertIsNone(data['buckets'][1]['avgHr'])

    def test_all_periods_merge_archive_and_realtime_without_duplicates(self):
        at = NOW - timedelta(hours=1)
        self.reading(at, hr=60, spo2=98, temp=38)
        self.reading(NOW, hr=120, spo2=96, temp=40)
        self.archive('2026-10-25T13:30:00+08:00,true,60,98,38\n')
        for period in ('day', 'week', 'month', 'all'):
            with self.subTest(period=period):
                data = self.get(period).get_json()
                self.assertEqual(data['summary']['heartRate']['count'], 2)
                self.assertEqual(data['summary']['heartRate']['avg'], 90)
                self.assertEqual(data['summary']['bodyTemp']['max'], 40)

    def test_archive_millisecond_precision_dedup_preserves_distinct_db_frames(self):
        self.reading((NOW - timedelta(seconds=1)).replace(microsecond=123456), hr=80)
        self.reading((NOW - timedelta(seconds=1)).replace(microsecond=123789), hr=80)
        self.archive('2026-10-25T06:29:59.123Z,true,80,99,38.2\n')
        data = self.get().get_json()
        self.assertEqual(data['summary']['heartRate']['count'], 2)

    def test_daily_buckets_merge_every_hour_and_all_includes_live_only(self):
        self.reading(NOW - timedelta(hours=2), hr=60)
        self.reading(NOW, hr=120)
        for period, size in [('week', 7), ('month', 30), ('all', 1)]:
            data = self.get(period).get_json()
            self.assertEqual(len(data['buckets']), size)
            self.assertEqual(data['buckets'][-1]['avgHr'], 90)
            self.assertEqual(data['buckets'][-1]['metrics']['heartRate']['count'], 2)

    def test_invalid_frames_and_metrics_are_not_counted(self):
        self.reading(NOW, valid=False)
        self.reading(NOW, hr=True, spo2=float('nan'), temp=float('inf'))
        self.reading(NOW, hr=-1, spo2=0, temp=None)
        self.reading(NOW, hr=80, spo2=None, temp=None)
        data = self.get().get_json()
        self.assertEqual(data['summary']['heartRate']['count'], 1)
        self.assertEqual(data['summary']['spo2']['count'], 0)
        self.assertIsNone(data['summary']['spo2']['avg'])
        self.assertEqual(sum(b['samples'] for b in data['buckets']), 1)

    def test_empty_and_single_point_have_honest_statistics(self):
        data = self.get().get_json()
        self.assertEqual(data['summary']['heartRate']['count'], 0)
        self.assertIsNone(data['actualRange'])
        self.reading(NOW, hr=81)
        data = self.get().get_json()
        self.assertEqual(data['summary']['heartRate'], {'count': 1, 'avg': 81, 'min': 81, 'max': 81})

    def test_archive_cache_invalidates_when_appended(self):
        path = self.archive('2026-10-25T05:00:00Z,true,60,98,38\n')
        self.assertEqual(self.get('all').get_json()['summary']['heartRate']['avg'], 60)
        with path.open('a') as handle:
            handle.write('2026-10-25T06:00:00Z,true,120,96,40\n')
        self.assertEqual(self.get('all').get_json()['summary']['heartRate']['avg'], 90)

    def test_bad_archive_exposes_partial_data_instead_of_silent_success(self):
        self.archive('bad-date,true,120,98,38\n')
        self.assertEqual(self.get('all').status_code, 503)
        self.reading(NOW)
        data = self.get('all').get_json()
        self.assertTrue(data['warnings'])
        self.assertEqual(data['summary']['heartRate']['count'], 1)

    def test_shanghai_midnight_and_sparse_all_history(self):
        self.archive('2026-10-23T15:59:00Z,true,80,98,38\n'
                     '2026-10-24T16:00:00Z,true,120,96,40\n')
        data = self.get('all').get_json()
        self.assertEqual([b['date'] for b in data['buckets']], ['2026-10-23', '2026-10-25'])
        self.assertEqual(data['coverage']['heartRate'], {'recordedBuckets': 2, 'totalBuckets': 3})

    def test_invalid_period_returns_400(self):
        self.assertEqual(self.get('wrong').status_code, 400)


if __name__ == '__main__':
    unittest.main()
