"""Durable product history: duplicates, gaps and invalid samples must not invent activity."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask
from sqlalchemy import delete, select
from helpers import fresh_db
from test_devices import seed_user, make_session_token
from petsense import db
from petsense.db.models import Device, Reading
from petsense.telemetry import observations


class ObservationTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        self.now = datetime(2026, 10, 25, 6, 30, tzinfo=timezone.utc)
        self.user = seed_user('observer')
        self.other = seed_user('other-observer')
        with db.session_scope() as s:
            s.add(Device(device_id='edge-test', owner_id=self.user, name='', claimed_at=self.now))
        app = Flask(__name__)
        app.register_blueprint(observations.bp)
        self.client = app.test_client()
        self.headers = {'X-Petsense-Session': make_session_token(self.user)}

    def frame(self, seq, **overrides):
        return {'deviceId': 'edge-test', 'source': 'esp32_mlp', 'boot_id': 'boot-a',
                'seq': seq, 'ts_ms': seq * 1000, 'modelVersion': 'v3',
                'action': {'label': 'walking', 'confidence': .9},
                'sound': {'label': 'bark', 'confidence': .8, 'experimental': False},
                'health': {'valid': True, 'heartRate': 100, 'spo2': 98, 'bodyTemp': 38},
                'battery': {'found': True, 'percent': 0}, **overrides}

    def ingest(self, seq, seconds=0, **overrides):
        with db.session_scope() as s:
            return observations.ingest_observation(s, self.frame(seq, **overrides),
                                                    self.now + timedelta(seconds=seconds))

    def get(self, period='day', headers=None):
        with patch.object(observations, 'utc_now', return_value=self.now + timedelta(seconds=20)):
            return self.client.get('/api/app/observations?period=' + period,
                                   headers=headers or self.headers)

    def test_duplicate_and_late_frames_do_not_change_samples_or_episodes(self):
        self.ingest(1)
        self.ingest(2, 1)
        self.assertFalse(self.ingest(2, 2))
        self.assertFalse(self.ingest(1, 3))
        result = self.get().json
        self.assertEqual(result['summary']['observedSeconds'], 1)
        self.assertEqual(result['summary']['walkingSeconds'], 1)
        self.assertEqual(result['summary']['soundEpisodeCount'], 1)
        self.assertEqual(result['events'][0]['frames'], 2)
        self.assertEqual(result['metrics']['heartRate']['count'], 2)
        self.assertEqual(result['latest']['payload']['battery']['percent'], 0)

    def test_gap_reboot_and_replayed_old_boot_never_fill_offline_time(self):
        self.ingest(1)
        self.ingest(2, 1)
        self.ingest(3, 10)
        self.ingest(0, 11, boot_id='boot-b')
        self.assertFalse(self.ingest(99, 12, boot_id='boot-a'))
        result = self.get().json
        self.assertEqual(result['summary']['observedSeconds'], 1)
        self.assertEqual(result['summary']['soundEpisodeCount'], 3)

    def test_quiet_zero_and_invalid_metrics_are_not_counted_as_sounds_or_health(self):
        self.ingest(1, sound={'label': 'quiet', 'confidence': 0},
                    health={'valid': False, 'heartRate': 900, 'spo2': 99, 'bodyTemp': 40})
        self.ingest(2, 1, sound={'label': 'quiet', 'confidence': 0},
                    health={'valid': True, 'heartRate': float('nan'), 'spo2': 0, 'bodyTemp': -1})
        result = self.get().json
        self.assertEqual(result['summary']['soundEpisodeCount'], 0)
        self.assertEqual(result['metrics']['heartRate']['count'], 0)
        self.assertIsNone(result['metrics']['heartRate']['avg'])

    def test_weighted_stats_and_real_extrema_survive_raw_deletion_and_new_request(self):
        self.ingest(1, -3700, health={'valid': True, 'heartRate': 60})
        self.ingest(2, -3699, health={'valid': True, 'heartRate': 180})
        self.ingest(3, 0, health={'valid': True, 'heartRate': 90})
        with db.session_scope() as s:
            s.execute(delete(Reading))
        db.Session.remove()
        result = self.get().json
        self.assertEqual(result['metrics']['heartRate'], {'avg': 110, 'min': 60, 'max': 180, 'count': 3})
        self.assertEqual(len(result['buckets']), 24)
        self.assertEqual(sum(b['metrics']['heartRate']['count'] == 0 for b in result['buckets']), 22)
        self.assertEqual(result['retentionDays'], 90)

    def test_short_quiet_gap_merges_episode_but_not_detection_duration(self):
        self.ingest(1)
        self.ingest(2, 1, sound={'label': 'quiet', 'confidence': 0})
        self.ingest(3, 2)
        result = self.get().json
        self.assertEqual(result['summary']['soundEpisodeCount'], 1)
        self.assertEqual(result['events'][0]['observedSeconds'], 0)

    def test_other_owner_and_unauthenticated_cannot_read_observations(self):
        self.ingest(1)
        self.assertEqual(self.client.get('/api/app/observations').status_code, 401)
        other = {'X-Petsense-Session': make_session_token(self.other)}
        self.assertIsNone(self.get(headers=other).json['latest'])
        self.assertEqual(self.get(headers=other).json['events'], [])
        self.assertEqual(self.get('bad').status_code, 400)

    def test_midnight_interval_splits_into_correct_days(self):
        self.now = datetime(2026, 10, 24, 15, 59, 59, tzinfo=timezone.utc)
        self.ingest(1)
        self.ingest(2, 2)
        result = self.get().json
        self.assertEqual(result['summary']['observedSeconds'], 1)
        self.assertEqual(result['summary']['walkingSeconds'], 1)

    def test_backfill_is_idempotent_and_uses_retained_hardware_frames(self):
        with db.session_scope() as s:
            for i in range(2):
                s.add(Reading(topic='petsense/sensor/edge_v1', device_id='edge-test',
                              payload=json.dumps(self.frame(i)), received_at=self.now + timedelta(seconds=i)))
        observations.backfill()
        observations.backfill()
        result = self.get().json
        self.assertEqual(result['metrics']['heartRate']['count'], 2)
        self.assertEqual(result['summary']['soundEpisodeCount'], 1)
