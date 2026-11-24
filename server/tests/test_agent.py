import json
import unittest
from datetime import timedelta
from unittest.mock import patch
from flask import Flask
from helpers import TurnClient, fresh_db, new_conversation
from test_pets import seed_user_with_pet
from petsense import db
from petsense.agent.conversation_routes import bp as conversation_bp
from petsense.agent.routes import bp
from petsense.db.models import AgentTurn, Pet, TelemetryMinute
from petsense.agent.observations import query
from sqlalchemy import select


def model_turn(text, calls=()):
    return {'text': text, 'function_calls': list(calls), 'usage': {}}


def allowed_stance(speaker='assistant', intent='read'):
    return ('{"scope":"allowed","intent":"%s","speaker":"%s"}\n' % (intent, speaker))


class AgentTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.auth = patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid}, None))
        self.auth.start()
        self.addCleanup(self.auth.stop)
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.client = TurnClient(self.app.test_client(), self.pet)
        self.app.register_blueprint(conversation_bp)  # 会话级已读接口（W16）
        conversation_auth = patch('petsense.agent.conversation_routes.require_app_user',
                                  return_value=({'id': self.uid}, None))
        conversation_auth.start()
        self.addCleanup(conversation_auth.stop)
        self.path = f'/api/app/agent/pets/{self.pet}/turns'

    def test_idempotency_and_restart_history(self):
        with patch('petsense.agent.model.respond', return_value=model_turn(allowed_stance('pet') + '【爱宠】汪！')) as respond:
            data = {'text': '你好', 'requestId': 'r1', 'target': 'pet'}
            first = self.client.post(self.path, json=data)
            second = self.client.post(self.path, json=data)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json, second.json)
            self.assertEqual(respond.call_count, 1)
        turn = first.json['turn']
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(turn['reply'], '【爱宠】汪！')
        self.assertEqual(turn['speaker'], 'pet')
        self.assertEqual(turn['blocks'], [{'type': 'text', 'speaker': 'pet', 'text': '汪！'}])
        self.assertEqual(len(self.client.get(self.path).json['turns']), 1)

    def test_stream_pushes_before_next_database_checkpoint(self):
        with patch('petsense.agent.routes.time') as clock, \
                patch('petsense.agent.routes.ws.push_delta') as push_delta, \
                patch('petsense.agent.routes.ws.push_turn') as push_turn, \
                patch('petsense.agent.model.respond') as respond:
            def stream(*args, on_delta, **kwargs):
                header = allowed_stance()
                for timestamp, text in [(10.00, ''), (10.01, '你'), (10.03, '你好'),
                                        (10.12, '你好呀'), (10.22, '你好呀！欢迎。')]:
                    clock.monotonic.return_value = timestamp
                    on_delta(header + text)
                with db.session_scope() as s:
                    pending = s.execute(select(AgentTurn)).scalar_one()
                    self.assertEqual(pending.reply, '你')
                    self.assertEqual(pending.status, 'pending')
                return model_turn(header + '你好呀！欢迎。')

            respond.side_effect = stream
            result = self.client.post(self.path, json={'text': '你好', 'requestId': 'stream'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual([call.args[3] for call in push_delta.call_args_list],
                         ['你', '你好呀', '你好呀！欢迎。'])
        self.assertEqual(result.json['turn']['status'], 'complete')
        push_turn.assert_called_once_with(self.uid, self.pet, result.json['turn'],
                                          conversation=result.json['conversation'])

    def test_owner_scope_and_invalid_input(self):
        with patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid + 1}, None)):
            self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.client.post(self.path, json={'text': 'hi'}).status_code, 400)

    def test_conversation_is_required_and_must_be_visible(self):
        """W16：取历史与发消息都必须指定本人名下、未删除的会话。"""
        raw = self.client.raw
        self.assertEqual(raw.get(self.path).status_code, 400)
        self.assertEqual(raw.get(f'{self.path}?conversationId=abc').status_code, 400)
        self.assertEqual(raw.get(f'{self.path}?conversationId=99999').status_code, 404)
        # TurnClient 没有显式给 conversationId 时才自动补上；这里显式给了一个不存在的会话
        missing = self.client.post(self.path, json={'text': 'hi', 'requestId': 'x', 'conversationId': 99999})
        self.assertEqual(missing.status_code, 404)
        with patch('petsense.agent.model.respond', return_value=model_turn(allowed_stance() + '你好')):
            first = self.client.post(self.path, json={'text': 'hi', 'requestId': 'y'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json['turn']['conversationId'], self.client.conversation_id)
        other = new_conversation(self.pet)
        self.assertEqual(self.client.get(f'{self.path}?conversationId={other}').json['turns'], [])

    def test_failure_preserves_owner_message(self):
        with patch('petsense.agent.model.respond', side_effect=ValueError('missing key')):
            response = self.client.post(self.path, json={'text': '今天怎样', 'requestId': 'r2'})
        self.assertEqual(response.json['turn']['status'], 'failed')
        self.assertEqual(self.client.get(self.path).json['turns'][0]['text'], '今天怎样')
        from petsense.db.models.agent import AgentRun
        with db.session_scope() as s:
            run = s.execute(select(AgentRun)).scalar_one()
            self.assertEqual(run.status, 'failed')
            self.assertIn('ValueError', run.error)

    def test_window_and_device_scope(self):
        end = db.utc_now().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=2)
        with db.session_scope() as s:
            for device, minute in [('esp32-001', start), ('esp32-001', end), ('other-device', start)]:
                s.add(TelemetryMinute(owner_id=self.uid, device_id=device, minute=minute,
                                      metrics='{}', actions='{"walking": 15}', observed_seconds=30, frames=30))
        with db.session_scope() as s:
            result = query(s, s.get(Pet, self.pet), start, end)
        self.assertEqual(result['minuteCount'], 1)
        self.assertEqual(result['actionSeconds']['walking'], 15)

    def test_conflicting_request_key_and_scope_gate(self):
        with patch('petsense.agent.model.respond', return_value=model_turn(allowed_stance() + '你好')):
            self.client.post(self.path, json={'text': '你好', 'requestId': 'same'})
            result = self.client.post(self.path, json={'text': '另一句话', 'requestId': 'same'})
        self.assertEqual(result.status_code, 409)
        refusal = model_turn('{"scope":"out_of_scope","intent":"read","speaker":"assistant"}\n'
                             '【照料助手】这个请求超出我的服务范围。')
        with patch('petsense.agent.model.respond', return_value=refusal) as respond, \
                patch('petsense.agent.registry.observations_query') as tool:
            result = self.client.post(self.path, json={'text': '宠物商城代码', 'requestId': 'scope'})
        self.assertEqual(result.json['turn']['status'], 'complete')
        self.assertEqual(result.json['turn']['sources'], [])
        respond.assert_called_once()
        tool.assert_not_called()
        from petsense.db.models.agent import AgentRun
        with db.session_scope() as s:
            run = s.execute(select(AgentRun).order_by(AgentRun.id.desc())).scalars().first()
            self.assertEqual(json.loads(run.conclusion)['scope'], 'out_of_scope')

    def test_tool_round_with_stance_collects_source_and_blocks(self):
        end = db.utc_now().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=2)
        with db.session_scope() as s:
            s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-001', minute=start,
                                  metrics='{}', actions='{"走动": 40}', observed_seconds=30, frames=30))
        args = json.dumps({'start': start.isoformat(), 'end': end.isoformat()})
        calls = [{'name': 'query_observations', 'arguments': args, 'call_id': 'c1'}]
        turns = [model_turn(allowed_stance(), calls),
                 model_turn(allowed_stance() + '【照料助手】这15分钟它走动了40秒。')]
        with patch('petsense.agent.model.respond', side_effect=turns):
            result = self.client.post(self.path, json={'text': '它刚才走动多吗', 'requestId': 'tool'})
        turn = result.json['turn']
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(len(turn['sources']), 1)
        self.assertEqual(turn['sources'][0]['observedSeconds'], 30)
        self.assertIn('soundEventCount', turn['sources'][0])
        self.assertEqual(turn['blocks'][0]['speaker'], 'assistant')
        self.assertIn('走动', turn['reply'])
        from petsense.db.models.agent import AgentRun
        with db.session_scope() as s:
            run = s.execute(select(AgentRun).order_by(AgentRun.id.desc())).scalars().first()
            summary = json.loads(run.tool_summary)
            self.assertEqual(summary[0]['name'], 'query_observations')
            self.assertTrue(summary[0]['allowed'])
            self.assertEqual(json.loads(run.conclusion)['scope'], 'allowed')

    def test_tool_call_without_stance_is_refused_then_allowed(self):
        args = json.dumps({'start': '2026-01-01T00:00:00+08:00', 'end': '2026-01-02T00:00:00+08:00'})
        calls = [{'name': 'query_observations', 'arguments': args, 'call_id': 'c1'}]
        turns = [model_turn('', calls),
                 model_turn(allowed_stance(), calls),
                 model_turn(allowed_stance() + '【照料助手】查到了。')]
        with patch('petsense.agent.model.respond', side_effect=turns), \
                patch('petsense.agent.registry.observations_query', wraps=query) as tool:
            result = self.client.post(self.path, json={'text': '它走了多久', 'requestId': 'stance'})
        self.assertEqual(result.json['turn']['status'], 'complete')
        self.assertEqual(tool.call_count, 1)
        from petsense.db.models.agent import AgentRun
        with db.session_scope() as s:
            run = s.execute(select(AgentRun).order_by(AgentRun.id.desc())).scalars().first()
            summary = json.loads(run.tool_summary)
            self.assertEqual(len(summary), 2)
            self.assertEqual(summary[0]['reason'], 'stance_missing')
            self.assertFalse(summary[0]['allowed'])
            self.assertIsNone(summary[1]['reason'])

    def test_pending_turn_recovers_as_interrupted(self):
        from petsense.db.models.agent import AgentTurn
        with db.session_scope() as s:
            s.add(AgentTurn(pet_id=self.pet, conversation_id=self.client.conversation_id, request_id='old',
                            text='原话', target='auto',
                            status='pending', reply='', created_at=db.utc_now() - timedelta(minutes=6)))
        turn = self.client.get(self.path).json['turns'][0]
        self.assertEqual(turn['status'], 'failed')
        self.assertEqual(turn['text'], '原话')

    def test_invalid_time_window(self):
        url = f'/api/app/agent/pets/{self.pet}/observations'
        self.assertEqual(self.client.get(url, query_string={'start': '2026-01-01', 'end': '2026-01-02'}).status_code, 400)

    def test_learning_coverage_is_durable_versioned_and_deduplicated(self):
        from petsense.telemetry.observations import ingest_observation
        from petsense.agent.learning import progress
        start = db.utc_now() + timedelta(seconds=1)
        frame = {'deviceId': 'esp32-001', 'source': 'esp32_mlp', 'boot_id': 'learn',
                 'modelVersion': 'v1', 'action': {'label': 'walking', 'confidence': .9},
                 'sound': {'label': 'quiet', 'confidence': .9}}
        with db.session_scope() as s:
            self.assertTrue(ingest_observation(s, dict(frame, seq=1), start))
            self.assertTrue(ingest_observation(s, dict(frame, seq=2), start + timedelta(seconds=1)))
            self.assertFalse(ingest_observation(s, dict(frame, seq=2), start + timedelta(seconds=1)))
        with db.session_scope() as s:
            values = progress(s, s.get(Pet, self.pet))
            self.assertEqual({v['dimension'] for v in values}, {'actions', 'sounds'})
            self.assertTrue(all(v['observedSeconds'] == 1 and v['validDays'] == 0 for v in values))
            minutes = s.execute(select(TelemetryMinute)).scalars().all()
            self.assertTrue(all('actions:v1' in minute.source_versions for minute in minutes if minute.observed_seconds > 0))

    def test_model_change_does_not_reuse_old_learning_progress(self):
        from petsense.db.models.agent import AgentLearningDay
        from petsense.agent.learning import progress
        now = db.utc_now()
        with db.session_scope() as s:
            for day in range(7):
                s.add(AgentLearningDay(pet_id=self.pet, device_id='esp32-001', feature='actions',
                    version='old', day=f'2026-10-{day+1:02}', seconds=21600, slots='[0,1]',
                    last_seen_at=now - timedelta(days=10-day)))
            s.add(AgentLearningDay(pet_id=self.pet, device_id='esp32-001', feature='actions',
                version='new', day='2026-11-13', seconds=30, slots='[0]', last_seen_at=now))
        with db.session_scope() as s:
            values = progress(s, s.get(Pet, self.pet))
        self.assertEqual(values[0]['version'], 'new')
        self.assertEqual(values[0]['validDays'], 0)
        self.assertEqual(values[1]['validDays'], 7)

    def test_read_state_persists_and_is_owner_scoped(self):
        """会话级已读（W16）：重复标记幂等，摘要里的未读数归零，别人的会话不可读。"""
        with patch('petsense.agent.model.respond', return_value=model_turn(allowed_stance() + '你好')):
            turn = self.client.post(self.path, json={'text': '你好', 'requestId': 'read'}).json['turn']
        url = f'/api/app/agent/conversations/{self.client.conversation_id}/read'
        first = self.client.post(url, json={'ids': [turn['id']]}).json
        second = self.client.post(url, json={'ids': [turn['id']]}).json
        self.assertIsNotNone(first['turns'][0]['readAt'])
        self.assertEqual((second['markedCount'], second['turns']), (0, []))  # 已读过的不再重复返回
        self.assertEqual(self.client.get(self.path).json['turns'][0]['readAt'],
                         first['turns'][0]['readAt'])  # 已读状态落库，重新读取不丢
        self.assertEqual((first['conversation']['unread'], first['markedCount']), (0, 1))
        self.assertEqual(self.client.post(url, json={'ids': []}).status_code, 400)
        with patch('petsense.agent.conversation_routes.require_app_user',
                   return_value=({'id': self.uid + 1}, None)):
            self.assertEqual(self.client.post(url, json={}).status_code, 404)
    def test_unavailable_capabilities_answered_without_tools(self):
        """未开放能力（联网/持续留意）由模型按注册表如实说明，不走固定文案分支；
        W03 起档案与设定写入工具已注册，不再属于「未开放」。"""
        with patch('petsense.agent.model.respond') as respond:
            respond.return_value = model_turn(allowed_stance('assistant', 'search')
                                               + '【照料助手】联网查证还没接入，先不能帮你搜。')
            response = self.client.post(self.path, json={'text': '帮我上网查一下这个粮的评价', 'requestId': 'write'})
        self.assertEqual(response.json['turn']['status'], 'complete')
        self.assertEqual(response.json['turn']['sources'], [])
        tools_offered = respond.call_args.kwargs.get('tools')
        # W06 起 EXTERNAL_READ 组含 read_web_page，随 allowed scope 一并挂载
        #（内建 web_search 由 loop 按 stance 决定是否挂载，不进 schemas）；
        # W09 起事实只读组另含 read_care_topics（主动提醒主题与依据）
        self.assertEqual({tool['name'] for tool in tools_offered},
                         {'query_observations', 'read_baseline', 'read_current_status', 'write_memory',
                          'search_memories', 'forget_memory', 'read_pet_profile', 'prepare_profile_update',
                          'prepare_role_settings', 'read_web_page', 'create_diary', 'draft_social_post',
                          'read_task', 'propose_task_change', 'read_care_topics'})
