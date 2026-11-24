"""双 Agent 群聊验收夹具的自检：夹具可用，并且真的走正式路径（真实鉴权、真实 app 装配、调度领取）。

夹具给 iOS 模拟器验收用（启动方式与剧本见 tests/fixtures/group_fixture_server.py 顶部说明）。这里保证它
不会随代码演进悄悄失效：剧本按触发句命中、控制接口能取到「路由 → 消息 → 运行 → 逐帧」的证据、
鉴权与归属在正式 app 上成立、断线与乱序注入确实发生。
"""
import importlib.util
import itertools
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from group_testkit import FakeSocket
from helpers import fresh_db
from simple_websocket import Client, ConnectionClosed
from sqlalchemy import select
from werkzeug.serving import make_server

from petsense.agent import group, jobs, ws
from petsense.db import session_scope
from petsense.db.models.agent import AgentTurn

TOKEN_HEADER = 'X-Petsense-Session'


def _load_fixture():
    """夹具是独立启动脚本（不在包内）：按路径加载，避免被 unittest discover 当成用例收集。"""
    spec = importlib.util.spec_from_file_location(
        'group_fixture_server', Path(__file__).resolve().parent / 'fixtures' / 'group_fixture_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE = _load_fixture()
_keys = itertools.count(1)


def complete(trace):
    """回合里的消息按 (角色, 状态, 正文) 列出。"""
    return [(message['role'], message['status'], message['text']) for message in trace['messages']]


def quiet_close(sock):
    """simple-websocket 对已关闭的连接再调 close 会抛 ConnectionClosed：清理时忽略。"""
    try:
        sock.close()
    except ConnectionClosed:
        pass


class FixtureTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        # 与夹具独立启动时一致：双 Agent 开启、全部账号放行、调度线程可用（用例自己触发调度）
        environment = patch.dict(os.environ, {'PETSENSE_MQTT_DISABLED': '1', 'PETSENSE_DUAL_AGENT_ENABLED': '1',
                                              'PETSENSE_SCHEDULER_DISABLED': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(FIXTURE.install_hooks())
        with FIXTURE.LOCK:
            FIXTURE.STATE.update(override=None, sticky=False, turns={}, frames=[], counts={}, last={})
        group.start()  # 正式 app 在 start_background 里注册；这里不启动后台线程，所以手动注册
        self.app = FIXTURE.build_app(start_background=False)
        self.client = self.app.test_client()
        login = self.client.post('/api/app/auth/login', json={'username': FIXTURE.SEED_USER,
                                                              'password': FIXTURE.SEED_PASSWORD})
        self.assertEqual(login.status_code, 200, login.get_data(as_text=True))
        self.auth = {TOKEN_HEADER: login.json['token']}
        self.uid = login.json['user']['id']
        pets = self.client.get('/api/app/pets', headers=self.auth).json['pets']
        self.pet = next(pet['id'] for pet in pets if pet['name'] == FIXTURE.SEED_PET)
        self.path = f'/api/app/agent/pets/{self.pet}/turns'

    def tearDown(self):
        self.assertTrue(group.wait_idle(30), '夹具里的群聊回合没有在限时内结束')

    # -- helpers ---------------------------------------------------------
    def connect(self):
        sock = FakeSocket()
        ws.clients[sock], ws.protocols[sock] = self.uid, True
        self.addCleanup(ws.clients.pop, sock, None)
        self.addCleanup(ws.protocols.pop, sock, None)
        return sock

    def send(self, text, **fields):
        response = self.client.post(self.path, headers=self.auth, json={
            'text': text, 'requestId': f'fixture-{next(_keys)}', 'protocol': group.PROTOCOL, **fields})
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        return response.json['turn']['id']

    def settle(self):
        jobs.tick()
        self.assertTrue(group.wait_idle(30))

    def trace(self, turn_id):
        response = self.client.get('/__test__/trace', query_string={'turn': turn_id})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def run_turn(self, text, **fields):
        turn_id = self.send(text, **fields)
        self.settle()
        return self.trace(turn_id)

    def turn_count(self):
        with session_scope() as s:
            return len(s.execute(select(AgentTurn)).scalars().all())

    # -- 控制接口与鉴权 ---------------------------------------------------
    def test_control_endpoints(self):
        health = self.client.get('/__test__/health').json
        self.assertEqual((health['ok'], health['protocol'], health['enabled'], health['scheduler']),
                         (True, 'group-v1', True, True))
        self.assertEqual(health['seed'], {'username': 'petsense', 'password': '123456', 'pet': '小白'})
        self.assertEqual(health['override'], {'name': None, 'sticky': False})
        listing = self.client.get('/__test__/scenarios').json['scenarios']
        self.assertEqual([item['name'] for item in listing], list(FIXTURE.SCENARIOS))
        for item in listing:
            self.assertEqual(sorted(item), ['expect', 'name', 'title', 'trigger'], item['name'])
            self.assertTrue(item['expect'], '每个剧本都要写明应观察到的现象')

    def test_turns_need_a_real_session(self):
        body = {'text': '陪我聊两句', 'requestId': 'no-auth', 'protocol': group.PROTOCOL}
        self.assertEqual(self.client.post(self.path, json=body).status_code, 401)
        self.assertEqual(self.client.post(self.path, json=body, headers={TOKEN_HEADER: 'forged'}).status_code, 401)
        self.assertEqual(self.client.get(self.path, query_string={'protocol': group.PROTOCOL}).status_code, 401)
        self.assertEqual(self.client.post(f'{self.path}/1/retry', json={}).status_code, 401)
        self.assertEqual(self.turn_count(), 0)

    def test_another_account_cannot_read_quote_or_retry(self):
        turn_id = self.send('陪我聊两句')
        self.settle()
        outsider = self.client.post('/api/app/auth/register', json={'username': 'outsider', 'password': '123456'})
        headers = {TOKEN_HEADER: outsider.json['token']}
        self.assertEqual(outsider.status_code, 200, outsider.get_data(as_text=True))
        self.assertEqual(self.client.get(self.path, headers=headers).status_code, 404)
        quoted = self.client.post(self.path, headers=headers, json={
            'text': '你好', 'requestId': 'outsider-1', 'replyTo': turn_id, 'protocol': group.PROTOCOL})
        self.assertEqual(quoted.status_code, 404)  # 别人的回合：与不存在同样处理
        retry = self.client.post(f'{self.path}/{turn_id}/retry', headers=headers, json={})
        self.assertEqual(retry.status_code, 404)
        self.assertEqual(self.trace(turn_id)['turn']['status'], 'complete')

    def test_legacy_client_keeps_the_old_protocol(self):
        response = self.client.post(self.path, headers=self.auth, json={'text': '你好', 'requestId': 'legacy-1'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(sorted(response.json), ['turn'])
        self.assertEqual(response.json['turn']['reply'], '【照料助手】' + FIXTURE.LEGACY_REPLY)
        # 没声明群聊协议的客户端不能点「两位」：拿到可识别的代码
        refused = self.client.post(self.path, headers=self.auth, json={
            'text': '你好', 'requestId': 'legacy-2', 'target': 'both'})
        self.assertEqual((refused.status_code, refused.json.get('code')), (400, 'dual_agent_unavailable'))

    # -- 关键路径：路由 → 记录 → WS → 逐帧取证 ------------------------------
    def test_turn_runs_through_the_real_stack_and_leaves_a_trace(self):
        sock = self.connect()
        trace = self.run_turn('陪我聊两句', target='both')  # App 里选「两位」时发送的指向
        self.assertEqual(trace['scenario'], 'dual')
        self.assertEqual([item['role'] for item in trace['route']['plan']], ['pet', 'assistant'])
        self.assertEqual(complete(trace), [
            ('pet', 'complete', '想你啦！今天有乖乖等你回家，走动的事让照料助手告诉你。'),
            ('assistant', 'complete', '我查了今天的记录：这项圈还没有上传走动数据，所以暂时给不出今天走了多久。')])
        # 每位一条独立轨迹：路由一次，爱宠与照料助手各一次，分别挂在自己的消息上
        runs = trace['runs']
        self.assertEqual([run['form'] for run in runs], ['route', 'dialogue', 'dialogue'])
        self.assertEqual([run['status'] for run in runs], ['complete'] * 3)
        self.assertEqual([run['messageId'] for run in runs[1:]],
                         [int(message['id'][1:]) for message in trace['messages']])
        self.assertEqual((trace['turn']['status'], trace['turn']['target']), ('complete', 'both'))
        # 逐帧：先是路由后的排队快照，再是领取、增量、终态，最后是整轮完成
        types = [frame['type'] for frame in trace['frames']]
        self.assertEqual((types[0], types[-1]), ('turn', 'turn'))
        self.assertLess(types.index('message'), types.index('message_delta'))
        self.assertLess(types.index('message_delta'), len(types) - 1)
        deltas = [frame for frame in trace['frames'] if frame['type'] == 'message_delta']
        self.assertEqual(sorted({frame['messageId'] for frame in deltas}),
                         sorted(message['id'] for message in trace['messages']))
        for message_id in {frame['messageId'] for frame in deltas}:
            own = [frame for frame in deltas if frame['messageId'] == message_id]
            versions = [frame['version'] for frame in own]
            self.assertEqual(versions, sorted(versions), '同一条消息的增量版本不回退')
            sizes = [frame['chars'] for frame in own]
            self.assertEqual(sizes, sorted(sizes), '增量是累计正文，只增不减')
        # 群聊连接实际收到的帧：messageId 是公开的字符串 id，与快照里的消息对得上
        sent = sock.frames
        self.assertEqual(sorted({frame['messageId'] for frame in sent if frame['type'] == 'message_delta'}),
                         sorted(message['id'] for message in trace['messages']))
        self.assertFalse([frame for frame in sent if frame['type'] == 'delta'])
        final = [frame for frame in sent if frame['type'] == 'turn'][-1]['turn']
        self.assertEqual([(message['id'], message['status']) for message in final['messages']],
                         [(message['id'], 'complete') for message in trace['messages']])
        listing = self.client.get(self.path, headers=self.auth, query_string={'protocol': group.PROTOCOL}).json
        self.assertEqual((listing['protocol'], listing['dualAgent']), ('group-v1', True))
        self.assertEqual([message['id'] for message in listing['turns'][-1]['messages']],
                         [message['id'] for message in trace['messages']])

    def test_fail_scenario_then_retry_reruns_only_the_failed_message(self):
        trace = self.run_turn('第二位失败', target='both')
        self.assertEqual(complete(trace)[:1], [('pet', 'complete', '我在呢，让照料助手帮你查。')])
        pet_id, assistant_id = (message['id'] for message in trace['messages'])
        self.assertEqual((trace['messages'][1]['role'], trace['messages'][1]['status'],
                          trace['messages'][1]['error']), ('assistant', 'failed', 'model_error'))
        self.assertEqual(trace['messages'][1]['text'], '')  # 中途出过的半截正文不保留
        response = self.client.post(f'{self.path}/{trace["turnId"]}/retry', headers=self.auth, json={})
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        self.assertEqual(response.json['target'], 'both')
        self.settle()
        trace = self.trace(trace['turnId'])
        self.assertEqual([message['id'] for message in trace['messages']], [pet_id, assistant_id])
        self.assertEqual([(run['messageId'], run['status']) for run in trace['runs'][2:]],
                         [(int(assistant_id[1:]), 'failed'), (int(assistant_id[1:]), 'complete')])
        self.assertEqual((trace['messages'][1]['status'], trace['messages'][1]['error']), ('complete', None))
        again = self.client.post(f'{self.path}/{trace["turnId"]}/retry', headers=self.auth, json={})
        self.assertEqual((again.status_code, again.json.get('code')), (409, 'nothing_to_retry'))

    def test_source_and_search_cards_belong_to_the_assistant_message(self):
        trace = self.run_turn('查一下来源')
        pet, assistant = trace['messages']
        self.assertEqual((pet['sources'], assistant['sources'][0]['type']), ([], 'observations'))
        pet_run = next(run for run in trace['runs'] if run['messageId'] == int(pet['id'][1:]))
        assistant_run = next(run for run in trace['runs'] if run['messageId'] == int(assistant['id'][1:]))
        self.assertEqual(pet_run['toolSummary'], [])
        self.assertEqual([(item['name'], item['allowed']) for item in assistant_run['toolSummary']],
                         [('query_observations', True)])

        trace = self.run_turn('联网核实一下')
        self.assertEqual([(message['role'], message['status']) for message in trace['messages']],
                         [('assistant', 'complete')])
        assistant = trace['messages'][0]
        self.assertEqual([source['url'] for source in assistant['sources']], [FIXTURE.FIXTURE_WEB['url']])
        statuses = [frame for frame in trace['frames'] if frame['type'] == 'message_status']
        self.assertEqual([(frame['messageId'], frame['status']) for frame in statuses],
                         [(assistant['id'], 'searching')])
        self.assertNotIn(FIXTURE.model.SEARCH_PROGRESS_HINT, assistant['text'])  # 状态不进正文
        self.assertEqual([(item['name'], item['reason']) for item in trace['runs'][-1]['toolSummary']],
                         [('web_search', 'server_side')])

    def test_single_speaker_scenarios(self):
        self.assertEqual(complete(self.run_turn('想我了吗')),
                         [('pet', 'complete', '想啦！一直趴在门口等你。')])
        trace = self.run_turn('今天走了多久')
        self.assertEqual([message['role'] for message in trace['messages']], ['assistant'])
        self.assertEqual(trace['turn']['target'], 'auto')  # 没选指向时由路由决定谁发言，回合如实保留 auto
        # 只点了照料助手时即使文字里有寒暄，爱宠也不插话
        self.assertEqual([item['kind'] for item in trace['route']['plan']], ['answer'])
        # 明确选「两位」时不看路由判断：两位都发言，爱宠在前
        both = self.run_turn('想我了吗', target='both')
        self.assertEqual(([message['role'] for message in both['messages']], both['turn']['target']),
                         (['pet', 'assistant'], 'both'))

    def test_named_pet_professional_request_is_split(self):
        trace = self.run_turn('我今天有点累')
        pet_run = next(run for run in trace['runs'] if run['messageId'] == int(trace['messages'][0]['id'][1:]))
        self.assertEqual((trace['messages'][0]['kind'], pet_run['toolSummary']), ('respond', []))
        self.assertIn('走动', trace['messages'][1]['text'])
        self.assertNotIn('走动数据', trace['messages'][0]['text'].split('让照料助手')[0])

    def test_drop_scenario_disconnects_the_client_and_the_turn_still_finishes(self):
        sock = self.connect()
        trace = self.run_turn('断线一下')
        self.assertEqual((sock.closed, sock.close_message), (None, 'fixture drop'))
        self.assertEqual([(message['role'], message['status']) for message in trace['messages']],
                         [('pet', 'complete'), ('assistant', 'complete')])
        closed = [frame for frame in trace['frames'] if frame['type'] == 'ws_closed']
        self.assertEqual([(frame['role'], frame['injected']) for frame in closed], [('assistant', 'drop')])

    def test_drop_scenario_closes_a_real_connection_and_the_turn_still_finishes(self):
        """假 socket 只记录调用：换成真正的 WebSocket 连接，确认对端确实收到了关闭、断开后回合照常走完。"""
        server = make_server('127.0.0.1', 0, self.app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        sock = Client.connect(f'{base.replace("http://", "ws://")}/api/app/agent/ws'
                              f'?token={self.auth[TOKEN_HEADER]}&protocol={group.PROTOCOL}')
        self.addCleanup(quiet_close, sock)
        sent = requests.post(f'{base}{self.path}', headers=self.auth, json={
            'text': '断线一下', 'requestId': f'fixture-{next(_keys)}', 'protocol': group.PROTOCOL}, timeout=10)
        self.assertEqual(sent.status_code, 202, sent.text)
        turn_id = sent.json()['turn']['id']

        settled = []

        def settle():
            try:
                self.settle()
            except Exception as exc:  # noqa: BLE001 断言失败带回主线程
                settled.append(exc)

        worker = threading.Thread(target=settle, daemon=True)
        worker.start()
        frames, closed = [], None
        deadline = time.monotonic() + 30
        while closed is None and time.monotonic() < deadline:
            try:
                raw = sock.receive(timeout=1)  # 超时返回 None；连接被关闭抛 ConnectionClosed
            except ConnectionClosed as exc:
                closed = (exc.reason, exc.message)
                break
            if raw and json.loads(raw).get('type') != 'ping':
                frames.append(json.loads(raw))
        worker.join(60)
        self.assertEqual(settled, [])
        self.assertEqual(closed, (1000, 'fixture drop'), '对端没有在限时内收到夹具发出的关闭')
        streamed = [frame['role'] for frame in frames if frame['type'] == 'message_delta']
        self.assertEqual((len(streamed) >= 2, streamed[-1]), (True, 'assistant'), '断开前正在收照料助手的增量')
        # 断开后的增量没有接收方，消息仍在后台走完；重连后按列表拉齐
        listing = requests.get(f'{base}{self.path}', headers=self.auth,
                               params={'protocol': group.PROTOCOL}, timeout=10).json()['turns']
        turn = next(item for item in listing if item['id'] == turn_id)
        self.assertEqual([(message['role'], message['status']) for message in turn['messages']],
                         [('pet', 'complete'), ('assistant', 'complete')])
        # 两次发言各自的运行都走完（轨迹里另有一次路由运行）
        self.assertEqual([run['status'] for run in self.trace(turn_id)['runs'] if run['messageId']],
                         ['complete', 'complete'])

    def test_order_scenario_injects_stale_and_duplicate_frames(self):
        sock = self.connect()
        trace = self.run_turn('乱序一下')
        injected = [frame['injected'] for frame in trace['frames'] if frame.get('injected')]
        self.assertEqual((injected.count('stale') > 0, injected.count('duplicate') > 0), (True, True))
        logged = [frame for frame in trace['frames'] if frame['type'] == 'message_delta']
        received = [frame for frame in sock.frames if frame['type'] == 'message_delta']
        self.assertEqual(len(received), len(logged))  # 注入帧确实发到了连接上（与真实帧无法区分）
        self.assertEqual([message['text'] for message in trace['messages']],
                         ['我来陪你。数据让照料助手说。',
                          '查到了：今天暂时没有走动数据，有数据时我会在这一条里告诉你。'])

    def test_router_failure_clarifies_once_without_tools(self):
        trace = self.run_turn('路由异常')
        route = trace['route']
        self.assertEqual((route['source'], route['scope'], [item['role'] for item in route['plan']]),
                         ('fallback', 'unknown', ['assistant']))
        self.assertEqual([run['status'] for run in trace['runs']], ['failed', 'complete'])
        self.assertEqual(trace['runs'][1]['toolSummary'], [])
        self.assertEqual([message['kind'] for message in trace['messages']], ['clarify'])
        self.assertEqual(complete(self.run_turn('想我了吗'))[0][:2], ('pet', 'complete'))  # 之后照常重新路由

    def test_scenario_override_and_reset(self):
        self.assertEqual(self.client.post('/__test__/scenario', json={'name': 'nope'}).status_code, 400)
        response = self.client.post('/__test__/scenario', json={'name': 'pet', 'sticky': True})
        self.assertEqual(response.json, {'ok': True, 'override': 'pet', 'sticky': True})
        trace = self.run_turn('随便说点什么')  # 不命中任何触发句：sticky 剧本优先
        self.assertEqual(trace['scenario'], 'pet')
        self.assertEqual(self.client.get('/__test__/health').json['override'],
                         {'name': 'pet', 'sticky': True})
        self.client.post('/__test__/reset')
        health = self.client.get('/__test__/health').json
        self.assertEqual((health['override'], health['turns']), ({'name': None, 'sticky': False}, 0))
        self.assertEqual(self.run_turn('随便说点什么')['scenario'], 'default')

    def test_one_shot_override_applies_to_the_next_message_only(self):
        self.client.post('/__test__/scenario', json={'name': 'assistant'})
        self.assertEqual(self.run_turn('随便说点什么')['scenario'], 'assistant')
        self.assertEqual(self.run_turn('随便说点什么')['scenario'], 'default')


if __name__ == '__main__':
    unittest.main()
