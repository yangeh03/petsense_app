"""W05 单元测试：基线计算、水位重算、read_baseline、快照/observations 共用结果（agent-w05-spec.md）。"""
import hashlib
import importlib.util
import json
import secrets
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask
from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import baseline, registry, snapshot
from petsense.agent.learning import record_interval
from petsense.agent.observations import query as observations_query
from petsense.agent.routes import bp
from petsense.common import shanghai_now
from petsense.db import utc_now
from petsense.db.models import AuthSession, Pet, TelemetryEvent, TelemetryMinute, User
from petsense.db.models.agent import AgentLearningDay, PetBaseline
from sqlalchemy import select

SHANGHAI = ZoneInfo('Asia/Shanghai')


def sh_day(offset):
    return (shanghai_now().date() + timedelta(days=offset)).isoformat()


def sh_local(day, hour, minute=0):
    return datetime.strptime(day, '%Y-%m-%d').replace(hour=hour, minute=minute,
                                                      tzinfo=SHANGHAI).astimezone(SHANGHAI)


class BaselineTestBase(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id, self.device = s.execute(
                select(Pet.id, Pet.device_id)).first()

    def seed_learning(self, days, feature='actions', version='v1', seconds=21600,
                      slots='[1,2]', last_offset_days=1):
        with db.session_scope() as s:
            for day in days:
                s.add(AgentLearningDay(pet_id=self.pet_id, device_id=self.device, feature=feature,
                                       version=version, day=day, seconds=seconds,
                                       last_seen_at=utc_now() - timedelta(days=last_offset_days),
                                       slots=slots))

    def seed_minutes(self, day, slot, count=70, walk=30, observed=60, version='v1',
                     features=('actions',)):
        with db.session_scope() as s:
            start = sh_local(day, slot * 6)
            for i in range(count):
                s.add(TelemetryMinute(owner_id=self.uid, device_id=self.device,
                                      minute=start + timedelta(minutes=i), metrics='{}',
                                      actions=json.dumps({'走动': walk} if 'actions' in features else {}),
                                      observed_seconds=observed if 'actions' in features else 0,
                                      sound_observed_seconds=observed if 'sounds' in features else 0,
                                      source_versions=json.dumps([f'{f}:{version}' for f in features])))

    def seed_events(self, day, slot, count, version='v1'):
        with db.session_scope() as s:
            start = sh_local(day, slot * 6)
            for i in range(count):
                moment = start + timedelta(minutes=i)
                s.add(TelemetryEvent(owner_id=self.uid, device_id=self.device, boot_id='b1',
                                     model_version=version, label='bark', started_at=moment,
                                     last_seen_at=moment + timedelta(seconds=5), frames=1,
                                     confidence_sum=0.9, observed_seconds=5))

    def _pet(self):
        with db.session_scope() as s:
            return s.get(Pet, self.pet_id)

    def read(self, **kwargs):
        pet = self._pet()
        with db.session_scope() as s:
            return baseline.read_baseline(s, pet, **kwargs)


class PercentileTests(unittest.TestCase):
    def test_nearest_rank(self):
        self.assertEqual(baseline._percentile(sorted([10, 20, 30, 40, 50, 60, 70]), 10), 10)
        self.assertEqual(baseline._percentile(sorted([10, 20, 30, 40, 50, 60, 70]), 50), 40)
        self.assertEqual(baseline._percentile(sorted([10, 20, 30, 40, 50, 60, 70]), 90), 70)
        self.assertEqual(baseline._percentile(sorted([1, 2, 3, 10]), 10), 1)
        self.assertEqual(baseline._percentile(sorted([1, 2, 3, 10]), 50), 2)
        self.assertEqual(baseline._percentile(sorted([1, 2, 3, 10]), 90), 10)

    def test_stats_shape(self):
        stats = baseline._stats([5, 1, 3])
        self.assertEqual(stats, {'samples': 3, 'p10': 1, 'p50': 3, 'p90': 5})
        self.assertIsNone(baseline._stats([]))


class DimensionStatesTests(BaselineTestBase):
    def test_states_and_active_flag(self):
        self.seed_learning([sh_day(-3), sh_day(-2), sh_day(-1)])
        self.seed_learning([sh_day(-1)], feature='sounds', last_offset_days=0)
        pet = self._pet()
        with db.session_scope() as s:
            states = baseline.dimension_states(s, pet)
        by_key = {(d['dimension'], d['version']): d for d in states}
        self.assertEqual(by_key[('actions', 'v1')]['state'], 'learning')
        self.assertEqual(by_key[('sounds', 'v1')]['state'], 'learning')
        self.assertTrue(by_key[('sounds', 'v1')]['active'])
        self.assertTrue(by_key[('actions', 'v1')]['active'])

    def test_ready_state_and_version_switch(self):
        old_days = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(old_days, version='v1')
        self.seed_learning([sh_day(-2), sh_day(-1)], version='v2', last_offset_days=0)
        pet = self._pet()
        with db.session_scope() as s:
            states = baseline.dimension_states(s, pet)
        by_key = {(d['dimension'], d['version']): d for d in states}
        self.assertEqual(by_key[('actions', 'v1')]['state'], 'ready')
        self.assertFalse(by_key[('actions', 'v1')]['active'])
        self.assertEqual(by_key[('actions', 'v2')]['state'], 'learning')
        self.assertTrue(by_key[('actions', 'v2')]['active'])


class SnapshotLineTests(BaselineTestBase):
    def test_learning_line_format(self):
        self.seed_learning([sh_day(-3), sh_day(-2), sh_day(-1)])
        pet = self._pet()
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertEqual(snap['sections']['learning'], '动作 3/7 天（学习中）')

    def test_learning_line_mixed_and_ready(self):
        ready = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(ready)
        self.seed_learning([sh_day(-1)], feature='sounds')
        pet = self._pet()
        with db.session_scope() as s:
            line = snapshot.assemble(s, pet, utc_now())['sections']['learning']
        self.assertEqual(line, '动作 已就绪、声音 1/7 天（学习中）')
        self.seed_learning([sh_day(-14 + i) for i in range(7)], feature='sounds')
        pet = self._pet()
        with db.session_scope() as s:
            line = snapshot.assemble(s, pet, utc_now())['sections']['learning']
        self.assertEqual(line, '动作 已就绪、声音 已就绪')


class RecomputeTests(BaselineTestBase):
    def test_watermark_idempotent(self):
        days = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(days)
        for day in days:
            self.seed_minutes(day, 1)
        pet = self._pet()
        with db.session_scope() as s:
            baseline.ensure_baseline(s, pet)
        with db.session_scope() as s:
            row = s.get(PetBaseline, (self.pet_id, 'actions', 'v1'))
            first_revision, first_window = row.revision, row.window_last_day
        pet = self._pet()
        with db.session_scope() as s:
            baseline.ensure_baseline(s, pet)  # 无新有效日：不重算
            row = s.get(PetBaseline, (self.pet_id, 'actions', 'v1'))
            self.assertEqual(row.revision, first_revision)
            self.assertEqual(row.window_last_day, first_window)
        new_day = sh_day(-1)
        self.seed_learning([new_day])
        self.seed_minutes(new_day, 1)
        pet = self._pet()
        with db.session_scope() as s:
            baseline.ensure_baseline(s, pet)
            row = s.get(PetBaseline, (self.pet_id, 'actions', 'v1'))
            self.assertEqual(row.revision, first_revision + 1)
            self.assertEqual(row.window_last_day, new_day)
            self.assertEqual(row.state, 'ready')

    def test_window_capped_at_28_days(self):
        days = [sh_day(-30 + i) for i in range(30)]
        self.seed_learning(days)
        for day in days:
            self.seed_minutes(day, 1)
        pet = self._pet()
        with db.session_scope() as s:
            baseline.ensure_baseline(s, pet)
            row = s.get(PetBaseline, (self.pet_id, 'actions', 'v1'))
        sample_days = json.loads(row.sample_days)
        self.assertEqual(len(sample_days), 28)
        self.assertNotIn(days[0], sample_days)
        self.assertNotIn(days[1], sample_days)
        self.assertEqual(row.window_last_day, days[-1])

    def test_pruned_detail_counts_progress_not_sample(self):
        recent = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(['2026-01-05'] + recent)  # 老有效日无分钟明细
        for day in recent:
            self.seed_minutes(day, 1)
        pet = self._pet()
        with db.session_scope() as s:
            baseline.ensure_baseline(s, pet)
            row = s.get(PetBaseline, (self.pet_id, 'actions', 'v1'))
        self.assertEqual(row.valid_days, 8)
        self.assertEqual(json.loads(row.sample_days), sorted(recent))
        payload = self.read()
        day = next(d for d in payload['dimensions'] if d['feature'] == 'actions')
        self.assertTrue(day['day']['ready'])  # 7 个有明细的样本仍就绪
        # 仅 5 个有明细样本时：维度 ready（8 个有效日）但全天范围样本不足不就绪
        with db.session_scope() as s:
            s.query(PetBaseline).delete()
            s.query(TelemetryMinute).filter(TelemetryMinute.minute < sh_local(recent[2], 6)).delete()
        payload = self.read()
        day = next(d for d in payload['dimensions'] if d['feature'] == 'actions')
        self.assertEqual(day['state'], 'ready')
        self.assertEqual(day['validDays'], 8)
        self.assertEqual(day['window']['sampleDays'], 5)
        self.assertFalse(day['day']['ready'])

    def test_slot_coverage_gate(self):
        days = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(days)
        for day in days:
            self.seed_minutes(day, 1, count=70, walk=30)   # 上午 70 分钟，样本有效
            self.seed_minutes(day, 2, count=30, walk=20)   # 下午 30 分钟覆盖，不足门槛
        payload = self.read()
        day = next(d for d in payload['dimensions'] if d['feature'] == 'actions')
        slots = {slot['slot']: slot for slot in day['slots']}
        self.assertTrue(slots[1]['ready'])
        self.assertEqual(slots[1]['samples'], 7)
        self.assertFalse(slots[2]['ready'])
        self.assertEqual(slots[2]['samples'], 0)


class ReadBaselineTests(BaselineTestBase):
    def setUp(self):
        super().setUp()
        self.window_days = [sh_day(-10 + i) for i in range(7)]  # D-10..D-4
        self.seed_learning(self.window_days)
        for day in self.window_days:
            self.seed_minutes(day, 1, count=70, walk=30)  # 上午 2100s
            self.seed_minutes(day, 2, count=70, walk=20)  # 下午 1400s
        self.target = sh_day(-3)
        self.seed_learning([self.target])
        self.seed_minutes(self.target, 1, count=70, walk=60)  # 当日上午 4200s（高于范围）
        self.seed_minutes(self.target, 2, count=70, walk=20)

    def _actions(self, payload):
        return next(d for d in payload['dimensions'] if d['feature'] == 'actions')

    def test_ready_payload_ranges_and_comparison(self):
        payload = self.read(date_str=self.target)
        self.assertEqual(payload['date'], self.target)
        dim = self._actions(payload)
        self.assertEqual(dim['state'], 'ready')
        self.assertEqual(dim['day']['samples'], 7)
        self.assertEqual(dim['day']['range'], {'p10': 3500, 'p50': 3500, 'p90': 3500})
        self.assertTrue(dim['day']['target']['comparable'])
        self.assertEqual(dim['day']['target']['band'], 'above')
        self.assertEqual(dim['day']['target']['value'], 5600)
        slots = {slot['slot']: slot for slot in dim['slots']}
        self.assertEqual(slots[1]['range'], {'p10': 2100, 'p50': 2100, 'p90': 2100})
        self.assertEqual(slots[1]['target']['band'], 'above')
        self.assertEqual(slots[2]['target']['band'], 'within')
        self.assertFalse(slots[0]['target']['comparable'])
        self.assertIn('覆盖不足', slots[0]['target']['note'])
        self.assertEqual(payload['source']['type'], 'baseline')
        self.assertNotIn('region', json.dumps(payload))
        self.assertNotIn('regions', json.dumps(payload))

    def test_date_excludes_same_and_later_days_from_samples(self):
        payload = self.read(date_str=self.target)
        dim = self._actions(payload)
        self.assertEqual(dim['window']['sampleDays'], 7)
        self.assertEqual(dim['window']['lastSampleDay'], self.window_days[-1])

    def test_learning_payload_no_ranges(self):
        with db.session_scope() as s:
            s.query(AgentLearningDay).delete()
            s.query(PetBaseline).delete()
        self.seed_learning([sh_day(-2), sh_day(-1)])
        payload = self.read()
        dim = self._actions(payload)
        self.assertEqual(dim['state'], 'learning')
        self.assertFalse(dim['comparable'])
        self.assertIn('认识中', dim['note'])
        self.assertNotIn('range', dim)
        self.assertNotIn('slots', dim)

    def test_sounds_dimension_events_metric(self):
        with db.session_scope() as s:
            s.query(AgentLearningDay).delete()
            s.query(PetBaseline).delete()
            s.query(TelemetryMinute).delete()
        sound_days = [sh_day(-10 + i) for i in range(7)]
        self.seed_learning(sound_days, feature='sounds')
        for day in sound_days:
            self.seed_minutes(day, 2, count=70, features=('sounds',))
            self.seed_events(day, 2, count=2)
        self.seed_learning([self.target], feature='sounds')
        self.seed_minutes(self.target, 2, count=70, features=('sounds',))
        self.seed_events(self.target, 2, count=6)
        payload = self.read(features=['sounds'], date_str=self.target)
        dim = payload['dimensions'][0]
        self.assertEqual(dim['metric'], '声音事件次数')
        self.assertEqual(dim['day']['range'], {'p10': 2, 'p50': 2, 'p90': 2})
        self.assertEqual(dim['day']['target']['value'], 6)
        self.assertEqual(dim['day']['target']['band'], 'above')

    def test_invalid_date_rejected(self):
        with self.assertRaises(ValueError):
            self.read(date_str='2026/11/01')
        with self.assertRaises(ValueError):
            self.read(date_str=sh_day(1))


class ToolAndRouteTests(BaselineTestBase):
    def setUp(self):
        super().setUp()
        with db.session_scope() as s:
            token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=self.uid, expires_at=utc_now() + timedelta(days=1)))
            self.auth = {'X-Petsense-Session': token}
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def _context(self):
        return {'pet_id': self.pet_id, 'user_id': self.uid}

    def test_tool_executes_and_yields_source(self):
        days = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(days)
        for day in days:
            self.seed_minutes(day, 1)
        gate = registry.execute_tool('read_baseline', {}, registry.DIALOGUE, 'allowed', self._context())
        self.assertTrue(gate['allowed'], gate['result'])
        self.assertEqual(gate['result']['source']['type'], 'baseline')
        self.assertEqual(len(gate['result']['dimensions']), 2)

    def test_tool_scope_and_feature_filter(self):
        gate = registry.execute_tool('read_baseline', {'feature': 'sounds'}, registry.DIALOGUE,
                                     'out_of_scope', self._context())
        self.assertFalse(gate['allowed'])
        self.assertEqual(gate['reason'], 'scope_not_allowed')
        gate = registry.execute_tool('read_baseline', {'feature': 'sounds'}, registry.DIALOGUE,
                                     'allowed', self._context())
        self.assertTrue(gate['allowed'])
        self.assertEqual([d['feature'] for d in gate['result']['dimensions']], ['sounds'])

    def test_http_endpoint_auth_and_ownership(self):
        response = self.client.get(f'/api/app/agent/pets/{self.pet_id}/baseline')
        self.assertEqual(response.status_code, 401)
        response = self.client.get('/api/app/agent/pets/9999/baseline', headers=self.auth)
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f'/api/app/agent/pets/{self.pet_id}/baseline?date=bad',
                                   headers=self.auth)
        self.assertEqual(response.status_code, 400)

    def test_http_endpoint_payload(self):
        days = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(days)
        for day in days:
            self.seed_minutes(day, 1)
        response = self.client.get(f'/api/app/agent/pets/{self.pet_id}/baseline', headers=self.auth)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.json
        self.assertIn('dimensions', payload)
        dim = next(d for d in payload['dimensions'] if d['feature'] == 'actions')
        self.assertEqual(dim['state'], 'ready')
        self.assertTrue(dim['day']['ready'])


class ObservationsStateTests(BaselineTestBase):
    def test_observations_learning_state(self):
        days = [sh_day(-9 + i) for i in range(7)]
        self.seed_learning(days)
        start = sh_local(days[0], 0)
        end = sh_local(sh_day(0), 0)
        pet = self._pet()
        with db.session_scope() as s:
            result = observations_query(s, pet, start, end)
        self.assertEqual(result['learning']['state'], 'ready')
        states = {(d['dimension'], d['version']): d for d in result['learningDimensions']}
        self.assertEqual(states[('actions', 'v1')]['state'], 'ready')
        self.assertTrue(states[('actions', 'v1')]['active'])

    def test_unbound_pet_learning_zero(self):
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            pet.device_id = ''
        payload = self.read()
        dim = next(d for d in payload['dimensions'] if d['feature'] == 'actions')
        self.assertEqual(dim['validDays'], 0)
        self.assertEqual(dim['state'], 'learning')


class RebuildToolTests(BaselineTestBase):
    """回填核验工具（spec §3.8）：重建逻辑、版本归属、跨日/跨时段切分与线上累计一致。"""

    def _rebuild(self):
        path = Path(__file__).resolve().parents[1] / 'tools' / 'agent_baseline_rebuild.py'
        spec = importlib.util.spec_from_file_location('agent_baseline_rebuild', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        connection = sqlite3.connect(db.engine().url.database)
        try:
            return module.rebuild(connection)
        finally:
            connection.close()

    def _seed_online(self, day, hour, count, walk=30, versions=('v1',)):
        """按线上真实路径造数据：分钟行先落库（无标注），record_interval 逐间隔记账并打标。"""
        with db.session_scope() as s:
            start = sh_local(day, hour)
            for i in range(count):
                moment = start + timedelta(minutes=i)
                s.add(TelemetryMinute(owner_id=self.uid, device_id=self.device,
                                      minute=moment.replace(second=0, microsecond=0), metrics='{}',
                                      actions=json.dumps({'走动': walk}), observed_seconds=60,
                                      sound_observed_seconds=0, source_versions='[]'))
                for version in versions:
                    record_interval(s, self.uid, self.device, 'actions', version, moment, 60)

    def _stored(self):
        with db.session_scope() as s:
            return {(row.feature, row.version, row.day): (row.seconds, sorted(json.loads(row.slots)))
                    for row in s.execute(select(AgentLearningDay)).scalars()}

    def _key(self, version, day):
        return (self.uid, self.device, 'actions', version, day)

    def test_rebuild_matches_online_accumulation(self):
        self._seed_online(sh_day(-2), 8, 180)          # 上午 3 小时
        self._seed_online(sh_day(-2), 12, 180)         # 下午 3 小时：合计 21600s、两个时段 → 有效日
        rebuilt, untagged = self._rebuild()
        stored = self._stored()
        self.assertEqual(untagged, 0)
        self.assertEqual(len(rebuilt), 1)
        key = self._key('v1', sh_day(-2))
        self.assertIn(key, rebuilt)
        self.assertEqual(rebuilt[key]['seconds'], 21600)
        self.assertEqual(sorted(rebuilt[key]['slots']), [1, 2])
        self.assertEqual(stored[('actions', 'v1', sh_day(-2))], (21600.0, [1, 2]))  # 与线上累计逐日一致

    def test_version_attribution_and_untagged(self):
        self._seed_online(sh_day(-1), 8, 30, versions=('v1',))
        self._seed_online(sh_day(-1), 14, 30, versions=('v2',))
        with db.session_scope() as s:  # 无标注历史分钟：单列计数、不归集任何版本
            start = sh_local(sh_day(-1), 20)
            for i in range(5):
                s.add(TelemetryMinute(owner_id=self.uid, device_id=self.device,
                                      minute=start + timedelta(minutes=i), metrics='{}',
                                      actions='{}', observed_seconds=60, sound_observed_seconds=0,
                                      source_versions='[]'))
        rebuilt, untagged = self._rebuild()
        self.assertEqual(untagged, 5)
        self.assertEqual(rebuilt[self._key('v1', sh_day(-1))]['seconds'], 1800)
        self.assertEqual(rebuilt[self._key('v2', sh_day(-1))]['seconds'], 1800)
        stored = self._stored()
        self.assertEqual(stored[('actions', 'v1', sh_day(-1))][0], 1800.0)
        self.assertEqual(stored[('actions', 'v2', sh_day(-1))][0], 1800.0)

    def test_cross_day_and_slot_split(self):
        self._seed_online(sh_day(-1), 23, 70)  # 23:00–00:10：跨上海日界与时段边界
        rebuilt, _ = self._rebuild()
        self.assertEqual(sorted(rebuilt), [self._key('v1', sh_day(-1)), self._key('v1', sh_day(0))])
        self.assertEqual(rebuilt[self._key('v1', sh_day(-1))]['seconds'], 3600)
        self.assertEqual(sorted(rebuilt[self._key('v1', sh_day(-1))]['slots']), [3])
        self.assertEqual(rebuilt[self._key('v1', sh_day(0))]['seconds'], 600)
        self.assertEqual(sorted(rebuilt[self._key('v1', sh_day(0))]['slots']), [0])
        stored = self._stored()
        self.assertEqual(stored[('actions', 'v1', sh_day(-1))], (3600.0, [3]))
        self.assertEqual(stored[('actions', 'v1', sh_day(0))], (600.0, [0]))


if __name__ == '__main__':
    unittest.main()
