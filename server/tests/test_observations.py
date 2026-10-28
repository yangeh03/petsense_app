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
            s.add(Device(device_id='edge-test', owner_id=self.user, name='', claimed_at=self.now - timedelta(days=1)))
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
        self.assertEqual(len(result['buckets']), 25)
        self.assertEqual(sum(b['metrics']['heartRate']['count'] == 0 for b in result['buckets']), 23)
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

    def test_transfer_never_exposes_previous_owner_history_or_bridges_duration(self):
        self.ingest(1)
        self.ingest(2, 1)
        with db.session_scope() as session:
            session.add(Reading(topic='petsense/sensor/edge_v1', device_id='edge-test', payload=json.dumps(self.frame(2)), received_at=self.now + timedelta(seconds=1)))
            device = session.get(Device, 'edge-test')
            device.owner_id = self.other
            device.claimed_at = self.now + timedelta(seconds=2)
        other = {'X-Petsense-Session': make_session_token(self.other)}
        before = self.get(headers=other).json
        self.assertIsNone(before['latest'])
        from petsense.telemetry.status import latest_telemetry
        self.assertIsNone(latest_telemetry('edge-test'))
        self.assertEqual(before['summary']['soundEpisodeCount'], 0)
        self.assertEqual(before['metrics']['heartRate']['count'], 0)
        self.assertIsNone(self.get().json['latest'])
        self.ingest(3, 2)
        after = self.get(headers=other).json
        self.assertEqual(after['summary']['observedSeconds'], 0)
        self.assertEqual(after['metrics']['heartRate']['count'], 1)
        self.assertEqual(after['summary']['soundEpisodeCount'], 1)
        self.assertEqual(after['events'][0]['frames'], 1)

    def test_model_change_starts_an_episode_without_previous_model_interval(self):
        self.ingest(1)
        self.ingest(2, 1, modelVersion='v4')
        result = self.get().json
        self.assertEqual(result['summary']['soundEpisodeCount'], 2)
        self.assertEqual(result['events'][0]['observedSeconds'], 0)

    def test_low_confidence_sound_cannot_start_duration_for_next_episode(self):
        self.ingest(1, sound={'label': 'bark', 'confidence': .1})
        self.ingest(2, 1)
        result = self.get().json
        self.assertEqual(result['events'][0]['frames'], 1)
        self.assertEqual(result['events'][0]['observedSeconds'], 0)

    def test_legacy_reboot_resumes_and_delayed_preboot_uptime_is_rejected(self):
        def legacy(uptime, seconds):
            payload = self.frame(uptime, boot_id=None, ts_ms=uptime)
            payload.pop('seq')
            with db.session_scope() as session:
                return observations.ingest_observation(session, payload, self.now + timedelta(seconds=seconds))
        self.assertTrue(legacy(100000, 0))
        self.assertTrue(legacy(1, 1))
        self.assertFalse(legacy(100001, 2))
        self.assertTrue(legacy(1001, 3))
        result = self.get().json
        self.assertEqual(result['latest']['payload']['ts_ms'], 1001)
        self.assertEqual(result['summary']['observedSeconds'], 2)

    def test_rejected_mqtt_delivery_is_not_broadcast_as_a_fresh_update(self):
        from types import SimpleNamespace
        from petsense.telemetry import mqtt_ingest
        packets = []
        def receive(packet, device):
            packets.append(json.loads(packet)['payload'])
        with patch.object(mqtt_ingest, 'broadcast_packet', side_effect=receive):
            for seq in [2, 1, 2]:
                msg = SimpleNamespace(topic='petsense/sensor/edge_v1', payload=json.dumps(self.frame(seq)).encode())
                mqtt_ingest.on_message(None, None, msg)
        self.assertEqual([json.loads(payload)['seq'] for payload in packets], [2])

    def test_pre_claim_backfill_is_ignored_and_retention_prunes_product_history(self):
        self.ingest(1, -2 * 86400)
        self.assertIsNone(self.get().json['latest'])
        self.ingest(2)
        with db.session_scope() as session:
            observations.prune(session, self.now + timedelta(days=91))
        result = self.get().json
        self.assertEqual(result['metrics']['heartRate']['count'], 0)
        self.assertEqual(result['events'], [])

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

    def test_day_timeline_runs_and_env_temp_metric(self):
        # 06:28 accumulates 11s of walking (walk run); 06:29 only standing (idle);
        # 06:30 has 1s of walking — below the threshold, so it stays idle, and the
        # offline stretch before 06:28 stays a no-data run.
        for i in range(12):
            self.ingest(i + 1, -120 + i)
        self.ingest(21, -60, action={'label': 'standing', 'confidence': .9})
        self.ingest(22, -59, action={'label': 'standing', 'confidence': .9})
        self.ingest(31, 0, health={'valid': True, 'heartRate': 100, 'spo2': 98, 'bodyTemp': 38, 'envTemp': 30})
        self.ingest(32, 2, health={'valid': True, 'heartRate': 110, 'spo2': 97, 'bodyTemp': 38.5, 'envTemp': 32})
        result = self.get().json
        self.assertEqual(result['metrics']['envTemp'], {'avg': 31, 'min': 30, 'max': 32, 'count': 2})
        timeline = result['timeline']
        self.assertEqual(timeline['startAt'], '2026-10-24T16:00:00.000Z')
        self.assertEqual(timeline['slotSeconds'], 60)
        self.assertEqual(timeline['runs'], [
            {'state': 'none', 'minutes': 868},
            {'state': 'walk', 'minutes': 1},
            {'state': 'idle', 'minutes': 2},
        ])

    def test_week_response_has_no_day_timeline(self):
        self.ingest(1)
        self.assertIsNone(self.get('week').json['timeline'])
