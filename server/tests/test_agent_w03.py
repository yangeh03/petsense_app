"""W03 单元测试：资料确认协议、助手设定与档案扩展（agent-w03-spec.md §6 单测层）。"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from helpers import fresh_db
from test_agent import allowed_stance, model_turn
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import proposals as proposal_service
from petsense.agent import registry, settings as settings_service
from petsense.agent.routes import bp as agent_bp
from petsense.db.models import Pet, PetWeightRecord
from petsense.db.models.agent import AgentProposal, CompanionSettings
from petsense.pets import routes as pets_routes
from petsense.pets.profile import (age_years, parse_birth_date, parse_measured_on,
                                   validate_weight_kg)
from sqlalchemy import select


class ProfileValidationTests(unittest.TestCase):
    def test_birth_formats_and_precision(self):
        parsed, precision = parse_birth_date('2023-09-10')
        self.assertEqual((parsed, precision), (date(2023, 9, 10), 'day'))
        parsed, precision = parse_birth_date('2023-09')
        self.assertEqual((parsed, precision), (date(2023, 9, 1), 'month'))
        parsed, precision = parse_birth_date('2023')
        self.assertEqual((parsed, precision), (date(2023, 1, 1), 'year'))
        # 显式精度只允许降档
        _, precision = parse_birth_date('2023-09-10', precision='month')
        self.assertEqual(precision, 'month')
        with self.assertRaises(ValueError):
            parse_birth_date('2023-09', precision='day')
        with self.assertRaises(ValueError):
            parse_birth_date('2024-13-01')
        with self.assertRaises(ValueError):
            parse_birth_date('not-a-date')

    def test_birth_window(self):
        with self.assertRaises(ValueError):
            parse_birth_date('2999-01-01')  # 未来
        with self.assertRaises(ValueError):
            parse_birth_date('1899-01-01')  # 早于合理窗口

    def test_weight_validation(self):
        self.assertEqual(validate_weight_kg(4.5), 4500)
        self.assertEqual(validate_weight_kg(0.1), 100)
        with self.assertRaises(ValueError):
            validate_weight_kg(0.05)
        with self.assertRaises(ValueError):
            validate_weight_kg(101)
        with self.assertRaises(ValueError):
            validate_weight_kg('九斤')

    def test_measured_on_window(self):
        self.assertEqual(parse_measured_on(None), parse_measured_on(None))  # 缺省今天
        self.assertEqual(parse_measured_on('2024-05-01'), date(2024, 5, 1))
        with self.assertRaises(ValueError):
            parse_measured_on('2999-01-01')
        with self.assertRaises(ValueError):
            parse_measured_on('05/01')

    def test_age_years(self):
        self.assertIsNone(age_years(None, None))
        from petsense.common import shanghai_now
        today = shanghai_now().date()
        exact, approximate = age_years(date(today.year - 3, 1, 1), 'day', today)
        self.assertEqual(exact, 3)
        self.assertFalse(approximate)
        _, approximate = age_years(date(today.year - 3, 1, 1), 'year', today)
        self.assertTrue(approximate)


class ProposalServiceTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalar_one().id

    def _pet(self, s):
        return s.get(Pet, self.pet_id)

    def test_normalize_rejects_unknown_and_conflicting(self):
        with self.assertRaises(proposal_service.ProposalError) as ctx:
            proposal_service.normalize_fields({'species': 'cat'})
        self.assertIn('species', ctx.exception.errors.get('fields', ''))
        with self.assertRaises(proposal_service.ProposalError) as ctx:
            proposal_service.normalize_fields({'birthDate': '2023-09-10', 'birthYear': 2023})
        self.assertIn('生日与估算年份', str(ctx.exception.errors))
        with self.assertRaises(proposal_service.ProposalError) as ctx:
            proposal_service.normalize_fields({'weightKg': 300})
        self.assertIn('体重', ctx.exception.errors['weightKg'])

    def test_normalize_keeps_display_kg_and_raw_text(self):
        payload = proposal_service.normalize_fields(
            {'weightKg': 4.5, 'weightRawText': '九斤', 'birthYear': 2023})
        self.assertEqual(payload['weightKg'], 4.5)
        self.assertNotIn('weightG', payload)
        self.assertEqual(payload['weightRawText'], '九斤')
        self.assertEqual(payload['birthDatePrecision'], 'year')
        self.assertEqual(payload['birthDate'][:4], '2023')

    def test_pending_creates_and_supersedes(self):
        with db.session_scope() as s:
            pet = self._pet(s)
            first, _, _ = proposal_service.create(s, pet, None, {'birthYear': 2023}, explicit=False)
            second, _, _ = proposal_service.create(s, pet, None, {'birthYear': 2022}, explicit=False)
            self.assertEqual(first.status, 'superseded')
            self.assertEqual(first.superseded_by, second.id)
            self.assertEqual(second.status, 'pending')
            self.assertEqual(second.version, 2)
            # pending 不写档案
            self.assertIsNone(pet.birth_date)

    def test_explicit_direct_write(self):
        with db.session_scope() as s:
            pet = self._pet(s)
            row, updated, _ = proposal_service.create(
                s, pet, None, {'birthDate': '2023-09-10', 'weightKg': 4.5, 'weightRawText': '九斤'},
                explicit=True)
            self.assertEqual(row.status, 'confirmed')
            self.assertEqual(pet.birth_date, date(2023, 9, 10))
            self.assertEqual(pet.birth_date_precision, 'day')
            record = s.execute(select(PetWeightRecord)).scalar_one()
            self.assertEqual(record.weight_g, 4500)
            self.assertEqual(record.raw_text, '九斤')
            self.assertEqual(record.source, 'agent')
            self.assertEqual(updated['birthDate'], '2023-09-10')

    def test_decide_confirm_reject_and_idempotency(self):
        with db.session_scope() as s:
            pet = self._pet(s)
            row, _, _ = proposal_service.create(s, pet, None, {'name': '豆豆'}, explicit=False)
            decided, _ = proposal_service.decide(s, pet, row.id, 'confirm')
            self.assertEqual(decided.status, 'confirmed')
            self.assertEqual(pet.name, '豆豆')
            again, _ = proposal_service.decide(s, pet, row.id, 'confirm')  # 幂等
            self.assertEqual(again.status, 'confirmed')
            with self.assertRaises(proposal_service.ProposalError) as ctx:
                proposal_service.decide(s, pet, row.id, 'reject')  # 终态异 action
            self.assertEqual(ctx.exception.status, 409)

    def test_decide_superseded_conflicts(self):
        with db.session_scope() as s:
            pet = self._pet(s)
            first, _, _ = proposal_service.create(s, pet, None, {'birthYear': 2023}, explicit=False)
            proposal_service.create(s, pet, None, {'birthYear': 2022}, explicit=False)
            with self.assertRaises(proposal_service.ProposalError) as ctx:
                proposal_service.decide(s, pet, first.id, 'confirm')
            self.assertEqual(ctx.exception.status, 409)
            self.assertIn('替代', ctx.exception.message)

    def test_decide_reject_keeps_profile(self):
        with db.session_scope() as s:
            pet = self._pet(s)
            row, _, _ = proposal_service.create(s, pet, None, {'name': '豆豆'}, explicit=False)
            decided, _ = proposal_service.decide(s, pet, row.id, 'reject')
            self.assertEqual(decided.status, 'rejected')
            self.assertEqual(pet.name, 'Max')


class RegistryToolTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalar_one().id
        self.context = {'pet_id': self.pet_id, 'user_id': self.uid, 'turn_id': None}

    def test_groups_and_scope_gate(self):
        self.assertEqual(registry._TOOLS['prepare_profile_update'].group, registry.PROFILE)
        self.assertEqual(registry._TOOLS['prepare_role_settings'].group, registry.SETTINGS)
        gate = registry.execute_tool('prepare_profile_update', {'fields': {}, 'explicit': False},
                                     'dialogue', 'out_of_scope', self.context)
        self.assertEqual(gate['reason'], 'scope_not_allowed')
        gate = registry.execute_tool('prepare_role_settings', {}, 'dialogue', None, self.context)
        self.assertEqual(gate['reason'], 'stance_missing')

    def test_read_pet_profile_payload(self):
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            pet.birth_date = date(2023, 1, 1)
            pet.birth_date_precision = 'year'
        gate = registry.execute_tool('read_pet_profile', {}, 'dialogue', 'allowed', self.context)
        result = gate['result']
        self.assertEqual(result['birthDatePrecision'], 'year')
        self.assertTrue(result['ageApproximate'])
        self.assertIsNone(result['latestWeight'])
        self.assertEqual(result['weightRecordCount'], 0)

    def test_prepare_profile_update_paths(self):
        gate = registry.execute_tool('prepare_profile_update',
                                     {'fields': {'birthYear': 2023}, 'explicit': False},
                                     'dialogue', 'allowed', self.context)
        self.assertTrue(gate['result']['written'] is False)
        self.assertEqual(gate['result']['proposal']['status'], 'pending')
        with db.session_scope() as s:
            self.assertIsNone(s.get(Pet, self.pet_id).birth_date)
        gate = registry.execute_tool('prepare_profile_update',
                                     {'fields': {'birthDate': '2023-09-10'}, 'explicit': True},
                                     'dialogue', 'allowed', self.context)
        self.assertTrue(gate['result']['written'])
        with db.session_scope() as s:
            self.assertEqual(s.get(Pet, self.pet_id).birth_date, date(2023, 9, 10))
        # 校验失败：结构化错误、不建提案
        gate = registry.execute_tool('prepare_profile_update',
                                     {'fields': {'weightKg': 999}, 'explicit': True},
                                     'dialogue', 'allowed', self.context)
        self.assertIn('error', gate['result'])
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentProposal)).scalars().all().__len__(), 2)

    def test_prepare_role_settings_direct_write(self):
        gate = registry.execute_tool('prepare_role_settings', {'assistantName': '小禾'},
                                     'dialogue', 'allowed', self.context)
        self.assertTrue(gate['result']['updated'])
        with db.session_scope() as s:
            row = s.get(CompanionSettings, self.uid)
            self.assertEqual(row.assistant_name, '小禾')


class AgentRouteTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(agent_bp)
        self.client = self.app.test_client()
        self.auth = patch('petsense.agent.routes.require_app_user',
                          return_value=({'id': self.uid}, None))
        self.auth.start()
        self.addCleanup(self.auth.stop)
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.path = f'/api/app/agent/pets/{self.pet}/turns'

    def test_turn_attaches_proposal_card_and_decision_flips_state(self):
        calls = [{'name': 'prepare_profile_update',
                  'arguments': json.dumps({'fields': {'birthYear': 2023}, 'explicit': False}),
                  'call_id': 'c1'}]
        turns = [model_turn(allowed_stance('assistant', 'write'), calls),
                 model_turn(allowed_stance('assistant', 'write')
                            + '【照料助手】它大概 2023 年出生，我整理成了候选，确认一下？')]
        with patch('petsense.agent.model.respond', side_effect=turns):
            result = self.client.post(self.path, json={'text': '它其实已经三岁了', 'requestId': 'p1'})
        turn = result.json['turn']
        card = [b for b in turn['blocks'] if b['type'] == 'proposal']
        self.assertEqual(len(card), 1)
        self.assertEqual(card[0]['proposal']['status'], 'pending')
        proposal_id = card[0]['proposal']['id']
        # 文本段仍只有角色消息（proposal 块不占 MAX_BLOCKS 名额）
        self.assertEqual(len([b for b in turn['blocks'] if b['type'] == 'text']), 1)

        decision_url = f'/api/app/agent/pets/{self.pet}/proposals/{proposal_id}/decision'
        decision = self.client.post(decision_url, json={'action': 'confirm'})
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json['proposal']['status'], 'confirmed')
        self.assertEqual(decision.json['turn']['blocks'][-1]['proposal']['status'], 'confirmed')
        with db.session_scope() as s:
            self.assertEqual(s.get(Pet, self.pet).birth_date_precision, 'year')
        # 幂等：重复确认同态返回
        again = self.client.post(decision_url, json={'action': 'confirm'})
        self.assertEqual(again.status_code, 200)
        # 异 action 409
        conflict = self.client.post(decision_url, json={'action': 'reject'})
        self.assertEqual(conflict.status_code, 409)

    def test_supersede_updates_old_card_and_conflict_reports_state(self):
        """v2 替代路径（D2）：旧回合卡片翻 superseded；对旧提案决策 409 且响应带当前状态（§5）。"""
        write_call = lambda year: [{'name': 'prepare_profile_update',
                                    'arguments': json.dumps({'fields': {'birthYear': year},
                                                             'explicit': False}),
                                    'call_id': 'c1'}]
        with patch('petsense.agent.model.respond', side_effect=[
                model_turn(allowed_stance('assistant', 'write'), write_call(2023)),
                model_turn(allowed_stance('assistant', 'write')
                           + '【照料助手】整理成候选了，确认一下？')]):
            first = self.client.post(self.path, json={'text': '它三岁了', 'requestId': 'v1'}).json['turn']
        with patch('petsense.agent.model.respond', side_effect=[
                model_turn(allowed_stance('assistant', 'write'), write_call(2022)),
                model_turn(allowed_stance('assistant', 'write')
                           + '【照料助手】按新的年份重新整理了一版。')]):
            second = self.client.post(self.path,
                                      json={'text': '不对，它是2022年的', 'requestId': 'v2'}).json['turn']
        turns = {t['id']: t for t in self.client.get(self.path).json['turns']}
        old_card = [b for b in turns[first['id']]['blocks'] if b['type'] == 'proposal'][0]
        new_card = [b for b in second['blocks'] if b['type'] == 'proposal'][0]
        self.assertEqual(old_card['proposal']['status'], 'superseded')
        self.assertEqual(new_card['proposal']['status'], 'pending')
        # 旧提案不可再确认：409 且响应带当前提案与其回合快照（App 按响应覆盖，按钮禁用兜底）
        conflict = self.client.post(
            f'/api/app/agent/pets/{self.pet}/proposals/{old_card["proposal"]["id"]}/decision',
            json={'action': 'confirm'})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json['proposal']['status'], 'superseded')
        self.assertEqual(conflict.json['turn']['id'], first['id'])
        stale_card = [b for b in conflict.json['turn']['blocks'] if b['type'] == 'proposal']
        self.assertEqual(stale_card[0]['proposal']['status'], 'superseded')

    def test_decision_reject_and_invalid(self):
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            row, _, _ = proposal_service.create(s, pet, None, {'name': '豆豆'}, explicit=False)
            proposal_id = row.id
        decision_url = f'/api/app/agent/pets/{self.pet}/proposals/{proposal_id}/decision'
        response = self.client.post(decision_url, json={'action': 'reject'})
        self.assertEqual(response.json['proposal']['status'], 'rejected')
        self.assertEqual(self.client.post(decision_url,
                                          json={'action': 'maybe'}).status_code, 400)
        self.assertEqual(self.client.post(
            f'/api/app/agent/pets/{self.pet}/proposals/9999/decision',
            json={'action': 'confirm'}).status_code, 404)
        with patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid + 1}, None)):
            self.assertEqual(self.client.post(
                decision_url, json={'action': 'confirm'}).status_code, 404)

    def test_assistant_settings_endpoints(self):
        default = self.client.get('/api/app/agent/assistant-settings')
        self.assertEqual(default.json['settings']['assistantName'], '照料助手')
        self.assertEqual(default.json['settings']['proactivity'], 1)
        updated = self.client.patch('/api/app/agent/assistant-settings',
                                    json={'assistantName': '小禾', 'ownerTitle': '妈妈',
                                          'verbosity': 'detailed', 'proactivity': 2})
        self.assertEqual(updated.json['settings']['assistantName'], '小禾')
        # 白名单与取值校验
        self.assertEqual(self.client.patch('/api/app/agent/assistant-settings',
                                           json={'nope': 1}).status_code, 400)
        self.assertEqual(self.client.patch('/api/app/agent/assistant-settings',
                                           json={'proactivity': 5}).status_code, 400)
        # ownerTitle 空串清除
        cleared = self.client.patch('/api/app/agent/assistant-settings', json={'ownerTitle': ''})
        self.assertIsNone(cleared.json['settings']['ownerTitle'])

    def test_settings_injected_into_system_prompt(self):
        with db.session_scope() as s:
            settings_service.persist(s, self.uid,
                                     [('assistant_name', '小禾'), ('owner_title', '妈妈')])
        with patch('petsense.agent.model.respond',
                   return_value=model_turn(allowed_stance() + '【照料助手】你好。')) as respond:
            self.client.post(self.path, json={'text': '你好', 'requestId': 's1'})
        system = respond.call_args.args[0][0]['content']
        self.assertIn('助手设定', system)
        self.assertIn('小禾', system)
        self.assertIn('妈妈', system)
        self.assertIn('【照料助手】', system)  # 身份标注不随改名变化


class PetsProfileTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(pets_routes.bp)
        self.client = self.app.test_client()
        self.auth = patch('petsense.pets.routes.require_app_user',
                          return_value=({'id': self.uid}, None))
        self.auth.start()
        self.addCleanup(self.auth.stop)
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id

    def test_patch_birth_formats_and_clear(self):
        for value, expected, precision in [('2023-09-10', date(2023, 9, 10), 'day'),
                                           ('2023-09', date(2023, 9, 1), 'month'),
                                           ('2023', date(2023, 1, 1), 'year')]:
            response = self.client.patch(f'/api/app/pets/{self.pet}', json={'birthDate': value})
            self.assertEqual(response.json['pet']['birthDate'], expected.isoformat())
            self.assertEqual(response.json['pet']['birthDatePrecision'], precision)
        cleared = self.client.patch(f'/api/app/pets/{self.pet}', json={'birthDate': None})
        self.assertIsNone(cleared.json['pet']['birthDate'])
        self.assertEqual(self.client.patch(f'/api/app/pets/{self.pet}',
                                           json={'birthDate': '2024-13'}).status_code, 400)

    def test_weight_records_crud_and_upsert(self):
        response = self.client.post(f'/api/app/pets/{self.pet}/weight-records',
                                    json={'weightKg': 4.5, 'measuredOn': '2026-11-01'})
        self.assertEqual(response.json['record']['weightKg'], 4.5)
        # 同日再记覆盖当日
        self.client.post(f'/api/app/pets/{self.pet}/weight-records',
                         json={'weightKg': 4.8, 'measuredOn': '2026-11-01'})
        self.client.post(f'/api/app/pets/{self.pet}/weight-records',
                         json={'weightKg': 5.0, 'measuredOn': '2026-11-10'})
        records = self.client.get(f'/api/app/pets/{self.pet}/weight-records').json['records']
        self.assertEqual([r['measuredOn'] for r in records], ['2026-11-10', '2026-11-01'])
        self.assertEqual(records[1]['weightKg'], 4.8)
        # pet_dict 最新体重（measured_on 最大）：经 GET /pets 序列化（pet_dict 在会话外读遥测）
        pets = self.client.get('/api/app/pets').json['pets']
        self.assertEqual(pets[0]['latestWeight']['measuredOn'], '2026-11-10')
        self.assertEqual(pets[0]['latestWeight']['weightKg'], 5.0)
        # 删除与归属
        record_id = records[0]['id']
        self.assertEqual(self.client.delete(
            f'/api/app/pets/{self.pet}/weight-records/{record_id}').status_code, 200)
        self.assertEqual(self.client.delete(
            f'/api/app/pets/{self.pet}/weight-records/{record_id}').status_code, 404)
        with patch('petsense.pets.routes.require_app_user', return_value=({'id': self.uid + 1}, None)):
            self.assertEqual(self.client.post(f'/api/app/pets/{self.pet}/weight-records',
                                              json={'weightKg': 5}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/app/pets/{self.pet}/weight-records',
                                          json={'weightKg': 500}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
