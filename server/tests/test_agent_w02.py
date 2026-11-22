"""W02 单元测试：氛围快照、read_current_status、注入与轨迹（agent-w02-spec.md）。"""
import hashlib
import json
import secrets
import unittest
from datetime import timedelta
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import loop, registry, runs, snapshot
from petsense.agent.routes import bp
from petsense.common import shanghai_now
from petsense.db import iso_z, utc_now
from petsense.db.models import AuthSession, Pet, TelemetryEvent, TelemetryLatest, TelemetryMinute, User
from petsense.db.models.agent import AgentLearningDay, AgentRun
from sqlalchemy import select

GPS_FRAME = {'source': 'A7670E_GNSS', 'crs': 'WGS84', 'enabled': True, 'valid': True,
             'status': 'fixed', 'latitude': 22.3, 'longitude': 113.5, 'utc': '2026-11-22T12:00:00Z'}


def latest_payload(action='walking', confidence=0.9, battery=76, source='esp32_mlp', gps=None):
    payload = {'source': source, 'battery': {'percent': battery},
               'health': {'heartRate': 120, 'spo2': 97, 'bodyTemp': 38.5, 'valid': True}}
    if action:
        payload['action'] = {'label': action, 'confidence': confidence}
    if gps:
        payload['gps'] = gps
    return json.dumps(payload)


def seed_latest(uid, device_id, payload, age_seconds=30):
    with db.session_scope() as s:
        s.add(TelemetryLatest(device_id=device_id, owner_id=uid, boot_id='b1', ordinal=1,
                              received_at=utc_now() - timedelta(seconds=age_seconds), payload=payload))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()

    def _pet(self):
        with db.session_scope() as s:
            return s.execute(select(Pet)).scalars().first()

    def test_ambient_with_fresh_frame(self):
        pet = self._pet()
        seed_latest(self.uid, pet.device_id, latest_payload(), age_seconds=30)
        now = utc_now()
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, now)
        current = snap['sections']['current']
        self.assertEqual(current['action'], '走动')
        self.assertTrue(current['online'])
        self.assertEqual(current['batteryPercent'], 76)
        self.assertLessEqual(current['ageSeconds'], 31)
        self.assertEqual(snap['meta']['trimmed'], [])
        self.assertEqual(snap['meta']['degraded'], [])
        self.assertIn('Max', json.dumps(snap['sections'], ensure_ascii=False))

    def test_offline_and_action_unavailable(self):
        pet = self._pet()
        seed_latest(self.uid, pet.device_id, latest_payload(action=None, source='edge_v2'),
                    age_seconds=config_online_age())
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        current = snap['sections']['current']
        self.assertFalse(current['online'])
        self.assertIsNone(current['action'])

    def test_unbound_and_no_data(self):
        pet = self._pet()
        with db.session_scope() as s:
            row = s.get(Pet, pet.id)
            row.device_id = ''
        pet = self._pet()
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertEqual(snap['sections']['current'], {'state': 'unbound'})

        pet = self._pet()
        with db.session_scope() as s:
            row = s.get(Pet, pet.id)
            row.device_id = 'esp32-other'
        pet = self._pet()
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertEqual(snap['sections']['current'], {'state': 'no_data'})

    def test_learning_line(self):
        pet = self._pet()
        with db.session_scope() as s:
            for day in ('2026-11-18', '2026-11-19', '2026-11-20'):
                s.add(AgentLearningDay(pet_id=pet.id, device_id=pet.device_id, feature='actions',
                                       version='v1', day=day, seconds=21600,
                                       last_seen_at=utc_now(), slots='["06:00","14:00"]'))
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertEqual(snap['sections']['learning'], '动作 3/7 天（学习中）')

    def test_trim_order_keeps_core(self):
        pet = self._pet()
        seed_latest(self.uid, pet.device_id, latest_payload())
        with patch('petsense.agent.snapshot.SNAPSHOT_CHAR_LIMIT', 290):
            with db.session_scope() as s:
                snap = snapshot.assemble(s, pet, utc_now())
        self.assertIn('learning', snap['meta']['trimmed'])
        self.assertNotIn('learning', snap['sections'])
        self.assertEqual(snap['sections']['current']['action'], '走动')
        with patch('petsense.agent.snapshot.SNAPSHOT_CHAR_LIMIT', 200):
            with db.session_scope() as s:
                snap = snapshot.assemble(s, pet, utc_now())
        self.assertEqual(snap['meta']['trimmed'], ['learning', 'current.secondary'])
        current = snap['sections']['current']
        for key in ('action', 'online', 'ageSeconds'):
            self.assertIn(key, current)
        self.assertIn('profile', snap['sections'])
        self.assertIn('now', snap['sections'])

    def test_snapshot_has_no_position_or_vitals(self):
        pet = self._pet()
        seed_latest(self.uid, pet.device_id, latest_payload(gps=GPS_FRAME))
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        serialized = json.dumps(snap['sections'], ensure_ascii=False)
        for forbidden in ('latitude', 'position', 'heartRate', '"x"'):
            self.assertNotIn(forbidden, serialized, f'快照不应包含 {forbidden}')

    def test_size_within_limit(self):
        pet = self._pet()
        seed_latest(self.uid, pet.device_id, latest_payload())
        with db.session_scope() as s:
            snap = snapshot.assemble(s, pet, utc_now())
        self.assertLessEqual(snap['meta']['chars'], snapshot.SNAPSHOT_CHAR_LIMIT)


def config_online_age():
    from petsense import config
    return config.DEVICE_ONLINE_TIMEOUT_S + 300


class CurrentStatusToolTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalar_one().id
        self.context = {'pet_id': self.pet_id, 'user_id': self.uid}

    def _seed_minutes(self, count=30, per_minute=30):
        now = utc_now()
        today_start = shanghai_now(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(now.tzinfo)
        expected_walk = 0
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            for i in range(count, 0, -1):
                minute = (now - timedelta(minutes=i)).replace(second=0, microsecond=0)
                s.add(TelemetryMinute(owner_id=self.uid, device_id=pet.device_id, minute=minute,
                                      metrics='{}', actions=json.dumps({'走动': per_minute, '站立': 60 - per_minute}),
                                      observed_seconds=60, frames=60))
                if minute >= today_start:
                    expected_walk += per_minute
        return expected_walk

    def test_buckets_today_and_device(self):
        from petsense.db.models import Device
        expected_walk = self._seed_minutes()
        seed_latest(self.uid, 'esp32-001', latest_payload(gps=GPS_FRAME))
        with db.session_scope() as s:  # 认领时间需早于遥测帧，latest_telemetry 才认这帧
            s.get(Device, 'esp32-001').claimed_at = utc_now() - timedelta(hours=1)
        gate = registry.execute_tool('read_current_status', {}, 'dialogue', 'allowed', self.context)
        self.assertTrue(gate['allowed'])
        self.assertIsNone(gate['reason'])
        result = gate['result']
        self.assertEqual(result['window']['minutes'], 30)
        self.assertTrue(5 <= len(result['actions']) <= 7, result['actions'])
        for bucket in result['actions']:
            self.assertEqual(bucket['topAction'], '走动')
            self.assertLessEqual(bucket['actionSeconds']['走动'], 5 * 30)
        self.assertEqual(result['today']['walkSeconds'], expected_walk)
        self.assertLessEqual(result['today']['observedMinutes'], 30)
        self.assertEqual(result['device']['batteryPercent'], 76)
        self.assertEqual(result['location']['outdoor']['latitude'], 22.3)

    def test_sounds_section(self):
        self._seed_minutes(count=2)
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            s.add(TelemetryEvent(owner_id=self.uid, device_id=pet.device_id, boot_id='b1',
                                 model_version='v1', label='bark',
                                 started_at=utc_now() - timedelta(minutes=5),
                                 last_seen_at=utc_now() - timedelta(minutes=4),
                                 frames=10, confidence_sum=8.0, observed_seconds=6, experimental=0))
        gate = registry.execute_tool('read_current_status', {'minutes': 10}, 'dialogue', 'allowed', self.context)
        result = gate['result']
        self.assertEqual(result['sounds']['count'], 1)
        self.assertEqual(result['sounds']['events'][0]['label'], 'bark')

    def test_minutes_clamped(self):
        seed_latest(self.uid, 'esp32-001', latest_payload())
        for raw, expected in ((0, 1), (300, 240), ('x', 30), (None, 30)):
            gate = registry.execute_tool('read_current_status', {'minutes': raw} if raw is not None else {},
                                         'dialogue', 'allowed', self.context)
            self.assertEqual(gate['result']['window']['minutes'], expected, raw)

    def test_unbound_pet_honest_sections(self):
        with db.session_scope() as s:
            s.get(Pet, self.pet_id).device_id = ''
        gate = registry.execute_tool('read_current_status', {}, 'dialogue', 'allowed', self.context)
        result = gate['result']
        self.assertEqual(result['device'], {'state': 'unbound'})
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['today']['soundEventCount'], 0)

    def test_invalid_frame_device_degrades_without_placeholder_zeros(self):
        # W02 复验 run14：无效帧 batteryPercent=0 被转述成「没电」——device 节必须降级、不给占位数值
        from petsense.db.models import Device
        seed_latest(self.uid, 'esp32-001',
                    json.dumps({'source': 'esp32_mlp', 'battery': {},
                                'health': {'heartRate': 0, 'spo2': 0, 'valid': False}}),
                    age_seconds=3600)
        with db.session_scope() as s:  # 认领时间早于帧
            s.get(Device, 'esp32-001').claimed_at = utc_now() - timedelta(hours=2)
        gate = registry.execute_tool('read_current_status', {}, 'dialogue', 'allowed', self.context)
        device = gate['result']['device']
        self.assertEqual(device['state'], 'stale_invalid')
        self.assertFalse(device['valid'])
        self.assertNotIn('batteryPercent', device)
        self.assertIn('占位', device['note'])
        self.assertTrue(device['receivedAt'])

    def test_matrix_gates(self):
        gate = registry.execute_tool('read_current_status', {}, 'dialogue', 'out_of_scope', self.context)
        self.assertEqual(gate['reason'], 'scope_not_allowed')
        gate = registry.execute_tool('read_current_status', {}, 'dialogue', None, self.context)
        self.assertEqual(gate['reason'], 'stance_missing')
        gate = registry.execute_tool('read_current_status', {}, 'review', 'allowed', self.context)
        self.assertEqual(gate['reason'], 'form_not_allowed')

    def test_registered_in_dialogue_schemas(self):
        names = [s['name'] for s in registry.schemas_for('dialogue', 'allowed')]
        # W06 新增 read_web_page（EXTERNAL_READ 组），快照与观测工具语义不变
        self.assertEqual(names, ['query_observations', 'read_current_status', 'read_web_page'])


class LoopInjectionTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalar_one().id

    def test_snapshot_injected_and_traced(self):
        snap = {'sections': {'profile': {'name': 'Max', 'species': 'dog', 'breed': ''},
                             'current': {'action': '走动', 'online': True, 'batteryPercent': 76, 'ageSeconds': 30}},
                'meta': {'chars': 180, 'trimmed': [], 'degraded': []}}
        turns = [{'text': '{"scope":"allowed","intent":"social","speaker":"pet"}\n【爱宠】我刚在走动呢！',
                  'function_calls': [], 'usage': {}}]
        with patch('petsense.agent.model.respond', side_effect=turns) as respond:
            outcome = loop.run_dialogue({'pet_id': self.pet_id, 'user_id': self.uid, 'text': '在干嘛',
                                         'target': 'auto', 'history': [], 'snapshot': snap,
                                         'now_iso': iso_z(utc_now())})
        system = respond.call_args_list[0].args[0][0]['content']
        self.assertIn('实时快照', system)
        self.assertIn('走动', system)
        self.assertIn('"batteryPercent": 76', system)
        self.assertEqual(outcome['trace']['snapshot']['sections']['current']['action'], '走动')
        self.assertEqual(outcome['trace']['snapshot']['chars'], 180)

    def test_trace_snapshot_saved_to_run(self):
        snap = {'sections': {'current': {'action': '躺着'}}, 'meta': {'chars': 40, 'trimmed': [], 'degraded': []}}
        turns = [{'text': '{"scope":"allowed","intent":"social","speaker":"assistant"}\n【照料助手】它在休息。',
                  'function_calls': [], 'usage': {}}]
        with patch('petsense.agent.model.respond', side_effect=turns):
            outcome = loop.run_dialogue({'pet_id': self.pet_id, 'user_id': self.uid, 'text': '在干嘛',
                                         'target': 'auto', 'history': [], 'snapshot': snap,
                                         'now_iso': iso_z(utc_now())})
        run_id = runs.save(self.pet_id, None, outcome['trace'], 'complete')
        summary = runs.by_id(run_id)
        self.assertEqual(summary['snapshot']['sections']['current']['action'], '躺着')

    def test_stance_missing_injects_one_time_reminder(self):
        # W02 复验 run16：被拒后模型自称「已重新发起」。机制：被拒轮后注入一次性系统提醒并进轨迹
        snap = {'sections': {'current': {'action': None}}, 'meta': {'chars': 30, 'trimmed': [], 'degraded': []}}
        call = {'name': 'query_observations',
                'arguments': '{"start":"2026-01-01T00:00:00+08:00","end":"2026-01-02T00:00:00+08:00"}',
                'call_id': 'c1'}
        turns = [
            {'text': '', 'function_calls': [call], 'usage': {}},  # 无 stance 首轮 → 被拒
            {'text': '{"scope":"allowed","intent":"read","speaker":"assistant"}\n【照料助手】刚才没查成，本次查询未完成。',
             'function_calls': [], 'usage': {}},
        ]
        with patch('petsense.agent.model.respond', side_effect=turns) as respond, \
                patch('petsense.agent.registry.observations_query') as tool:
            tool.return_value = {'observedSeconds': 0, 'source': {'type': 'observations'}}
            outcome = loop.run_dialogue({'pet_id': self.pet_id, 'user_id': self.uid, 'text': '最近一小时声音',
                                         'target': 'auto', 'history': [], 'snapshot': snap,
                                         'now_iso': iso_z(utc_now())})
        second_round_items = respond.call_args_list[1].args[0]
        reminder = [item for item in second_round_items if item.get('role') == 'user' and '系统提醒' in str(item.get('content'))]
        self.assertEqual(len(reminder), 1, '被拒轮后应注入恰好一条系统提醒')
        self.assertIn('禁止声称已重新查询成功', reminder[0]['content'])
        self.assertIn({'stance_retry_reminder': True}, outcome['trace']['messages'])
        self.assertEqual(tool.call_count, 0, '无 stance 时工具不得执行')


class RouteIntegrationTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        with db.session_scope() as s:
            user = User(username='w02-user', display_name='w02-user', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user)
            s.flush()
            self.uid = user.id
            pet = Pet(user_id=user.id, name='Max', species='狗', breed='柯基',
                      created_at=db.utc_now(), device_id='esp32-001')
            s.add(pet)
            s.flush()
            self.pet_id = pet.id
            token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
            self.auth = {'X-Petsense-Session': token}

    def test_post_persists_run_snapshot(self):
        seed_latest(self.uid, 'esp32-001', latest_payload(), age_seconds=10)
        turns = [{'text': '{"scope":"allowed","intent":"social","speaker":"pet"}\n【爱宠】汪！我在呢。',
                  'function_calls': [], 'usage': {}}]
        with patch('petsense.agent.model.respond', side_effect=turns):
            response = self.client.post(f'/api/app/agent/pets/{self.pet_id}/turns', headers=self.auth,
                                        json={'text': '在干嘛', 'requestId': 'w02-int'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with db.session_scope() as s:
            run = s.execute(select(AgentRun).where(AgentRun.pet_id == self.pet_id)
                            .order_by(AgentRun.id.desc())).scalars().first()
        self.assertIsNotNone(run.snapshot)
        snap = json.loads(run.snapshot)
        self.assertEqual(snap['sections']['current']['action'], '走动')
        self.assertEqual(snap['sections']['profile']['name'], 'Max')


if __name__ == '__main__':
    unittest.main()
