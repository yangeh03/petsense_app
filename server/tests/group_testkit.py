"""双 Agent 群聊测试公共件：脚本化模型桩、假 WS 连接与隔离的测试基类。

模型一律不打真实接口：Script 按调用种类（爱宠 / 照料助手 / 旧路径）与轮次返回脚本里的内容，
并记录每次调用的输入，供断言「谁拿到了什么提示、什么工具」。

W18 起没有前置意图路由：派发由主人的选择决定，模型桩只按角色分种类。
"""

import json
import os
import time
import unittest
from datetime import timedelta
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from sqlalchemy import select
from test_pets import seed_user_with_pet

from petsense.agent import group, jobs, loop, model, runs, ws
from petsense.agent import routes as agent_routes
from petsense.agent.inbox_routes import bp as inbox_bp
from petsense.db import session_scope, utc_now
from petsense.db.models import Pet, User
from petsense.db.models.agent import AgentJob, AgentRoute, AgentRun, AgentTurn
from petsense.db.models.memory import PetMemory

PROTOCOL = group.PROTOCOL
MEMORY_ARGS = {'kind': 'owner_event', 'subject': 'owner', 'summary': '主人今天带我去公园跑了两圈'}
_keys = iter(range(1, 10 ** 6))


def next_key():
    return f'k-{next(_keys)}'


def classify(input_items):
    """识别这次模型调用的种类：看系统提示（与验收夹具同一判据）。"""
    system = input_items[0].get('content') if input_items else ''
    system = system if isinstance(system, str) else ''
    if system.startswith(loop._PET_SYSTEM_PROMPT[:24]):
        return 'pet'
    if system.startswith(loop._GROUP_ASSISTANT_HEAD[:24]):
        return 'assistant'
    return 'legacy'


def user_texts(input_items):
    """这次调用里全部 user 消息的纯文本（内容块数组按顺序取出）。"""
    texts = []
    for item in input_items:
        if item.get('role') != 'user':
            continue
        content = item.get('content')
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(block.get('text', '') for block in content if isinstance(block, dict))
    return texts


def tool_names(schemas):
    return [schema.get('name') or schema.get('function', {}).get('name') for schema in schemas or []]


class Script:
    """按种类与轮次脚本化的模型桩。

    每个条目：字符串 = 分两段出字的正文；dict = 自定义一轮：
      {'text': …} 正文            {'calls': [(工具名, 参数)]} 工具轮      {'raise': True} 抛错
      {'hint': True} 先推联网进度提示    {'web': True} 带回网页来源
      {'chunks': n} 正文分 n 段出字      {'gate': Event} 两段之间等待（测试借此停在生成中途）
    轮次用完后一律回「我在。」。
    """

    def __init__(self, **scripts):
        # 每个种类的脚本是一轮一个条目的列表；直接给一个字符串或一个 dict 时只有一轮
        self.scripts = {kind: ([value] if isinstance(value, (str, dict)) else list(value))
                        for kind, value in scripts.items()}
        self.calls = []
        self.counts = {}

    def of(self, kind):
        return [call for call in self.calls if call['kind'] == kind]

    def __call__(self, input_items, tools=None, on_delta=None, max_output_tokens=1800, web_search=False):
        kind = classify(input_items)
        index = self.counts.get(kind, 0)
        self.counts[kind] = index + 1
        self.calls.append({'kind': kind, 'items': input_items, 'tools': tool_names(tools),
                           'web_search': web_search, 'max_output_tokens': max_output_tokens})
        steps = self.scripts.get(kind) or []
        step = steps[index] if index < len(steps) else '我在。'
        text = step if isinstance(step, str) else step.get('text', '')
        if isinstance(step, dict):
            if step.get('hint') and on_delta:
                on_delta(model.SEARCH_PROGRESS_HINT)
            if step.get('raise'):
                raise RuntimeError('scripted failure')
        calls = []
        if isinstance(step, dict):
            for number, (name, arguments) in enumerate(step.get('calls') or []):
                calls.append({'name': name, 'call_id': f'call_{kind}_{index}_{number}',
                              'arguments': json.dumps(arguments, ensure_ascii=False)})
        if text:
            emit = on_delta or (lambda _text: None)  # 旧路径的调用不带流式回调：只返回完整正文
            pieces = step.get('chunks') if isinstance(step, dict) and on_delta else None
            head, sep, body = text.partition('\n')
            if pieces:
                size = max(1, -(-len(text) // pieces))
                for offset in range(0, len(text), size):
                    emit(text[:offset + size])
                    time.sleep(0.08)  # 段间隔超过 WS 节流窗口、小于落库间隔：多段共享同一落库版本
            elif sep and head.startswith('{'):
                # 范围声明行先出（主人看不到），正文再分两段：与服务端的流式行为一致
                emit(head + sep + body[:len(body) // 2])
                time.sleep(0.08)
                emit(text)
            else:
                emit(text[:len(text) // 2] or text)
                gate = step.get('gate') if isinstance(step, dict) else None
                if gate is not None:
                    gate.wait(15)
                elif on_delta:
                    time.sleep(0.08)  # 超过 WS 节流窗口：两段增量都会被推送
                emit(text)
        result = {'text': text, 'function_calls': calls, 'usage': {}}
        if isinstance(step, dict) and step.get('web'):
            result.update(web_search_calls=[{'id': 'ws_1', 'status': 'completed', 'query': 'q', 'openPage': ''}],
                          citations=[{'url': 'https://example.com/source', 'title': '来源', 'snippet': '摘要'}])
        return result


def stance_json(scope='allowed', intent='read'):
    """照料助手的范围声明行（W18：范围由它自己这一次调用给出，服务端剥离、不上屏）。"""
    return json.dumps({'scope': scope, 'intent': intent, 'speaker': 'assistant'}, ensure_ascii=False)


def say(text='', scope='allowed', intent='read', **extra):
    """照料助手的一轮回复：首行范围声明 + 正文（服务端把声明行剥离，主人只看到正文）。"""
    step = {'text': stance_json(scope, intent) + ('\n' + text if text else '')}
    step.update(extra)
    return step


def pet_says(text='', reason='这题需要查证数据'):
    """爱宠的一轮：先说一句陪伴话，再调用转交工具把专业部分交给照料助手。"""
    return {'text': text, 'calls': [('handoff_to_assistant', {'reason': reason})]}


class FakeSocket:
    """只实现了推帧用到的接口；断开连接的真实行为（关闭帧、对端是否收到）要靠真实连接验证。"""

    def __init__(self):
        self.frames = []
        # 与 simple_websocket.Base.close 同口径：reason 是数字状态码，message 是给对端的文字
        self.closed = None
        self.close_message = None

    def send(self, text):
        self.frames.append(json.loads(text))

    def close(self, reason=None, message=None):
        self.closed = reason
        self.close_message = message


def legacy_turn(pet_id, text, reply='', blocks=None, speaker='assistant', status='complete', sources=None):
    """旧路径留下的回合（没有消息行），用来验证历史投影与兼容。"""
    with session_scope() as s:
        turn = AgentTurn(pet_id=pet_id, request_id=next_key(), text=text, target='auto', reply=reply,
                         sources=json.dumps(sources or [], ensure_ascii=False), speaker=speaker, status=status,
                         blocks=json.dumps(blocks, ensure_ascii=False) if blocks else None, created_at=utc_now())
        s.add(turn)
        s.flush()
        return turn.id


def other_user_pet():
    """另一位主人和它的宠物（验证归属：别人的回合不可引用、不可重试）。返回宠物 id。"""
    with session_scope() as s:
        user = User(username='u2', display_name='u2', password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=utc_now())
        s.add(user)
        s.flush()
        s.add(Pet(user_id=user.id, name='Rex', species='dog', breed='', avatar_emoji='🐶',
                  device_id=None, created_at=utc_now()))
        s.flush()
        return s.execute(select(Pet.id).where(Pet.user_id == user.id)).scalar_one()


class GroupBase(unittest.TestCase):
    """开关全开、调度线程关闭（用例自己触发）的隔离环境；模型由 use() 指定的脚本代替。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        environment = patch.dict(os.environ, {'PETSENSE_DUAL_AGENT_ENABLED': '1',
                                              'PETSENSE_SCHEDULER_DISABLED': ''})
        environment.start()
        self.addCleanup(environment.stop)
        group.start()
        app = Flask(__name__)
        app.register_blueprint(agent_routes.bp)
        app.register_blueprint(inbox_bp)
        self.client = app.test_client()
        for target in ('petsense.agent.routes.require_app_user', 'petsense.agent.inbox_routes.require_app_user'):
            auth = patch(target, return_value=({'id': self.uid, 'username': 'u1'}, None))
            auth.start()
            self.addCleanup(auth.stop)
        with session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.path = f'/api/app/agent/pets/{self.pet}/turns'

    def tearDown(self):
        # 模型桩与开关仍然生效时等后台线程结束（cleanup 在 tearDown 之后才还原它们）
        self.assertTrue(group.wait_idle(30), '群聊回合没有在限时内结束')

    # -- helpers ---------------------------------------------------------
    def use(self, **scripts):
        script = Script(**scripts)
        patcher = patch.object(model, 'respond', side_effect=script)
        patcher.start()
        self.addCleanup(patcher.stop)
        return script

    def connect(self, group_protocol=True):
        sock = FakeSocket()
        ws.clients[sock] = self.uid
        ws.protocols[sock] = group_protocol
        self.addCleanup(ws.clients.pop, sock, None)
        self.addCleanup(ws.protocols.pop, sock, None)
        return sock

    def post(self, text, key=None, protocol=True, **fields):
        body = {'text': text, 'requestId': key or next_key()}
        if protocol:
            body['protocol'] = PROTOCOL
        body.update(fields)
        return self.client.post(self.path, json=body)

    def accepted(self, text='陪我聊聊', **fields):
        response = self.post(text, **fields)
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        return response.json['turn']['id']

    def settle(self):
        """正式触发路径：调度领取作业 → 线程池生成；等本进程的任务结束。"""
        jobs.tick()
        self.assertTrue(group.wait_idle(30), '群聊回合没有在限时内结束')

    def wait_until(self, predicate, timeout=10):
        limit = time.monotonic() + timeout
        while time.monotonic() < limit:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def get(self, protocol=True, **query):
        """GET turns：protocol=True 为群聊协议读取（带 messages），否则是旧客户端读取。"""
        if protocol:
            query = dict(query, protocol=PROTOCOL)
        return self.client.get(self.path, query_string=query)

    def settle_job(self, turn_id):
        """回合作业在回合结束后要到下一次复查才结束：按复查间隔推进调度并断言它已结束。"""
        jobs.tick(utc_now() + timedelta(seconds=group.RECHECK_S + 1))
        return self.job_row(turn_id).status

    def rows(self, turn_id):
        with session_scope() as s:
            return [group.message_public(row) for row in group.message_rows(s, [turn_id]).get(turn_id, [])]

    def turn_row(self, turn_id):
        with session_scope() as s:
            return s.get(AgentTurn, turn_id)

    def route_row(self, turn_id):
        with session_scope() as s:
            return s.execute(select(AgentRoute).where(AgentRoute.turn_id == turn_id)).scalar_one()

    def runs_of(self, turn_id):
        with session_scope() as s:
            rows = s.execute(select(AgentRun).where(AgentRun.turn_id == turn_id)
                             .order_by(AgentRun.id)).scalars().all()
            return [runs.summarize(row) for row in rows]

    def job_row(self, turn_id):
        with session_scope() as s:
            return s.execute(select(AgentJob).where(AgentJob.dedup_key == group.job_key(turn_id))).scalar_one()

    def group_jobs(self):
        with session_scope() as s:
            return s.execute(select(AgentJob).where(AgentJob.kind == group.TURN_KIND)).scalars().all()

    def count(self, model):
        with session_scope() as s:
            return len(s.execute(select(model)).scalars().all())

    def memory_count(self):
        with session_scope() as s:
            return len(s.execute(select(PetMemory)).scalars().all())

    def retry(self, turn_id, pet_id=None):
        return self.client.post(f'/api/app/agent/pets/{pet_id or self.pet}/turns/{turn_id}/retry', json={})
