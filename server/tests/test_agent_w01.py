"""W01 内核单元测试：循环协议、注册表矩阵、轨迹与 WS 通道（agent-w01-spec.md）。"""
import hashlib
import json
import secrets
import threading
import time
import unittest
from datetime import timedelta
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import loop, registry, runs, ws
from petsense.agent.routes import bp
from petsense.db.models import AuthSession, Pet, User, TelemetryMinute
from sqlalchemy import select


def model_turn(text, calls=()):
    return {'text': text, 'function_calls': list(calls), 'usage': {}}


class LoopProtocolTests(unittest.TestCase):
    def test_parse_stance_variants(self):
        stance, body = loop.parse_stance('{"scope":"allowed","intent":"read","speaker":"pet"}\n【爱宠】汪')
        self.assertEqual(stance, {'scope': 'allowed', 'intent': 'read', 'speaker': 'pet'})
        self.assertEqual(body, '【爱宠】汪')
        stance, body = loop.parse_stance('普通回答，没有声明')
        self.assertIsNone(stance)
        self.assertEqual(body, '普通回答，没有声明')
        stance, body = loop.parse_stance('{"scope":"weird"}\n正文')
        self.assertEqual(stance['scope'], 'unknown')
        stance, body = loop.parse_stance('')
        self.assertIsNone(stance)

    def test_delta_filter_strips_stance_line(self):
        seen = []
        stream = loop.DeltaFilter(seen.append)
        stream.feed('{"scope":"allowed","inte')
        self.assertEqual(seen, [])
        stream.feed('{"scope":"allowed","intent":"read","speaker":"assistant"}\n你')
        stream.feed('{"scope":"allowed","intent":"read","speaker":"assistant"}\n你好呀')
        self.assertEqual(seen, ['你', '你好呀'])
        seen.clear()
        stream2 = loop.DeltaFilter(seen.append)
        stream2.feed('直接回答')
        stream2.feed('直接回答，续')
        self.assertEqual(seen, ['直接回答', '直接回答，续'])

    def test_split_blocks(self):
        blocks, overflow = loop.split_blocks('【爱宠】汪！【照料助手】它今天很精神。', 'assistant')
        self.assertEqual([b['speaker'] for b in blocks], ['pet', 'assistant'])
        self.assertFalse(overflow)
        blocks, _ = loop.split_blocks('开场白【爱宠】汪', 'assistant')
        self.assertEqual([(b['speaker'], b['text']) for b in blocks],
                         [('assistant', '开场白'), ('pet', '汪')])
        blocks, overflow = loop.split_blocks('【爱宠】一【照料助手】二【爱宠】三', 'assistant')
        self.assertEqual(len(blocks), 2)
        self.assertTrue(overflow)

    def test_budget_exhaustion_degrades_to_no_tools(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            pet_id = s.execute(select(Pet)).scalar_one().id
        calls = [{'name': 'query_observations',
                  'arguments': '{"start":"2026-01-01T00:00:00+08:00","end":"2026-01-02T00:00:00+08:00"}',
                  'call_id': 'c'}]
        turns = [model_turn('{"scope":"allowed","intent":"read","speaker":"assistant"}', calls)
                 for _ in range(loop.MAX_TOOL_ROUNDS)]
        turns.append(model_turn('{"scope":"allowed","intent":"read","speaker":"assistant"}\n【照料助手】查询预算用完了。'))
        with patch('petsense.agent.model.respond', side_effect=turns) as respond, \
                patch('petsense.agent.registry.observations_query') as tool:
            tool.return_value = {'observedSeconds': 0, 'source': {'type': 'observations'}}
            outcome = loop.run_dialogue({'pet_id': pet_id, 'user_id': self.uid, 'text': '问',
                                         'snapshot': {'sections': {'current': {'action': None}},
                                                      'meta': {'chars': 40, 'trimmed': [], 'degraded': []}},
                                         'target': 'auto', 'history': [], 'now_iso': '2026-11-22T00:00:00Z'})
        self.assertTrue(outcome['degraded'])
        self.assertIn('tool_rounds_exhausted', outcome['trace']['degraded'])
        self.assertEqual(tool.call_count, loop.MAX_TOOL_ROUNDS)
        self.assertEqual(respond.call_count, loop.MAX_TOOL_ROUNDS + 1)
        last_call_tools = respond.call_args_list[-1].kwargs.get('tools')
        self.assertFalse(last_call_tools)


class RegistryMatrixTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.context = {'pet_id': self.pet, 'user_id': self.uid}

    def test_unknown_tool_refused(self):
        gate = registry.execute_tool('does_not_exist', {}, 'dialogue', 'allowed', self.context)
        self.assertFalse(gate['allowed'])
        self.assertEqual(gate['reason'], 'unknown_tool')
        self.assertIn('不存在', gate['result']['error'])

    def test_scope_gate_blocks_business_tools(self):
        args = {'start': '2026-01-01T00:00:00+08:00', 'end': '2026-01-02T00:00:00+08:00'}
        gate = registry.execute_tool('query_observations', args, 'dialogue', 'out_of_scope', self.context)
        self.assertFalse(gate['allowed'])
        self.assertEqual(gate['reason'], 'scope_not_allowed')
        gate = registry.execute_tool('query_observations', args, 'dialogue', None, self.context)
        self.assertEqual(gate['reason'], 'stance_missing')

    def test_form_gate(self):
        args = {'start': '2026-01-01T00:00:00+08:00', 'end': '2026-01-02T00:00:00+08:00'}
        gate = registry.execute_tool('query_observations', args, 'review', 'allowed', self.context)
        self.assertFalse(gate['allowed'])
        self.assertEqual(gate['reason'], 'form_not_allowed')

    def test_bad_arguments_structured(self):
        gate = registry.execute_tool('query_observations', None, 'dialogue', 'allowed', self.context)
        self.assertEqual(gate['reason'], 'bad_arguments')
        gate = registry.execute_tool('query_observations', {'start': '不是时间'}, 'dialogue', 'allowed', self.context)
        self.assertTrue(gate['allowed'])
        self.assertIn('error', gate['result'])

    def test_naive_time_interpreted_as_shanghai_and_granularity(self):
        end = db.utc_now().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=2)
        with db.session_scope() as s:
            s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-001', minute=start,
                                  metrics='{}', actions='{"走动": 30}', observed_seconds=30, frames=30))
        args = {'start': '2026-11-22', 'end': '2026-11-23'}
        with patch('petsense.agent.registry._parse_time', side_effect=lambda v: start if v == '2026-11-22' else end):
            gate = registry.execute_tool('query_observations', args, 'dialogue', 'allowed', self.context)
        self.assertTrue(gate['allowed'])
        self.assertEqual(gate['result']['actionSeconds']['走动'], 30)
        summary = registry.execute_tool('query_observations',
                                        {'start': '2026-11-22', 'end': '2026-11-23', 'granularity': 'summary'},
                                        'dialogue', 'allowed', self.context)
        self.assertNotIn('soundEvents', summary['result'])
        self.assertNotIn('latest', summary['result'])

    def test_schemas_for_scope(self):
        self.assertEqual(registry.schemas_for('dialogue', 'out_of_scope'), [])
        names = [s['name'] for s in registry.schemas_for('dialogue', 'allowed')]
        self.assertEqual(names, ['query_observations', 'read_baseline', 'read_current_status'])
        self.assertEqual(registry.schemas_for('review', 'allowed'), [])


class RunTrajectoryTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id

    def test_save_and_replay(self):
        trace = {'form': 'dialogue', 'trigger': {'target': 'auto', 'text': '问'},
                 'conclusion': {'scope': 'allowed', 'intent': 'read', 'speaker': 'assistant', 'reason_code': 'ok'},
                 'messages': [{'role': 'assistant', 'text': '答'}], 'tool_summary': [],
                 'budget': {'rounds': 0, 'wall_seconds': 1.2}, 'degraded': []}
        run_id = runs.save(self.pet, None, trace, 'complete')
        summary = runs.by_id(run_id)
        self.assertEqual(summary['conclusion']['scope'], 'allowed')
        self.assertEqual(summary['messages'], trace['messages'])
        self.assertEqual(runs.by_id(run_id + 999), None)


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = None

    def send(self, data):
        self.sent.append(json.loads(data))

    def close(self, status=1000):
        self.closed = status

    def receive(self):
        return None


class BlockingWS(FakeWS):
    """receive 阻塞到 release，用于让 handler 线程保持连接注册状态。"""

    def __init__(self):
        super().__init__()
        self.release = __import__('threading').Event()

    def receive(self):
        self.release.wait(5)
        return None


class AgentWsTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        ws.clients.clear()
        self.app = Flask(__name__)
        from petsense.telemetry import realtime
        realtime.sock.init_app(self.app)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.addCleanup(ws.clients.clear)
        with db.session_scope() as s:
            user = User(username='ws-user', display_name='ws-user', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user)
            s.flush()
            self.uid = user.id
            token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
            self.token = token

    def _connect(self, token=None, blocking=False):
        socket = BlockingWS() if blocking else FakeWS()
        query = f'?token={token if token is not None else "bad"}'
        if blocking:
            def run():
                with self.app.test_request_context(f'/api/app/agent/ws{query}'):
                    ws.handler(socket)
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            for _ in range(100):
                if socket in ws.clients:
                    break
                time.sleep(0.02)
            return socket
        with self.app.test_request_context(f'/api/app/agent/ws{query}'):
            ws.handler(socket)
        return socket

    def test_unauthorized_rejected(self):
        socket = self._connect(token='bad')
        self.assertEqual(socket.sent[0]['type'], 'unauthorized')
        self.assertIsNotNone(socket.closed)
        self.assertEqual(ws.clients, {})

    def test_authorized_receives_scoped_frames(self):
        socket = self._connect(self.token, blocking=True)
        self.assertIn(socket, ws.clients)
        ws.push_delta(self.uid, 7, 12, '部分回复', 'assistant')
        ws.push_turn(self.uid, 7, {'id': 12, 'status': 'complete'})
        self.assertEqual([f['type'] for f in socket.sent], ['delta', 'turn'])
        self.assertEqual(socket.sent[0]['text'], '部分回复')
        self.assertEqual(socket.sent[1]['turn']['id'], 12)
        ws.push(self.uid + 999, {'type': 'delta', 'text': '别人的'})
        self.assertEqual(len(socket.sent), 2)
        socket.release.set()

    def test_ping_frame(self):
        socket = self._connect(self.token, blocking=True)
        from petsense.agent.ws import _send
        _send(socket, {'type': 'ping'})
        self.assertEqual(socket.sent[-1]['type'], 'ping')
        socket.release.set()
