"""W01 首批行为评测（E1–E10）：走完整 HTTP 往返 + AgentRun 轨迹断言（真实模型）。

运行方式（本地或云端，需 MINIMAX_API_KEY(_FILE)）：
  cd server && python3 -m unittest agent_behavior.test_agent_behavior -v
无凭据环境整组 skip（CI 默认 skip）。场景定义见模块级 SCENARIOS 与各 test_* 方法。
"""
import hashlib
import json
import os
import secrets
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from helpers import fresh_db

from petsense import db
from petsense.agent.routes import bp
from petsense.db.models import AuthSession, Pet, User, TelemetryMinute
from petsense.db.models.agent import AgentRun
from sqlalchemy import select

SHANGHAI = ZoneInfo('Asia/Shanghai')


def _has_credentials():
    return bool(os.environ.get('MINIMAX_API_KEY') or os.environ.get('MINIMAX_API_KEY_FILE'))


def _latest_run(pet_id):
    with db.session_scope() as s:
        return s.execute(select(AgentRun).where(AgentRun.pet_id == pet_id)
                         .order_by(AgentRun.id.desc())).scalars().first()


class BehaviorScenarioTests(unittest.TestCase):
    """每个场景独立建库：真实观测种子 + 真实模型往返 + 轨迹断言。"""

    @classmethod
    def setUpClass(cls):
        if not _has_credentials():
            raise unittest.SkipTest('未配置 MINIMAX 凭据，行为评测整组 skip')
        cls.longMessage = True

    def setUp(self):
        fresh_db(self)
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        with db.session_scope() as s:
            user = User(username='eval', display_name='eval', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user)
            s.flush()
            self.uid = user.id
            pet = Pet(user_id=user.id, name='Max', species='狗', breed='柯基',
                      created_at=db.utc_now(), device_id='esp32-eval')
            s.add(pet)
            s.flush()
            self.pet = pet.id
            token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
            self.auth = {'X-Petsense-Session': token}
        self.path = f'/api/app/agent/pets/{self.pet}/turns'

    def _seed_yesterday(self, walk_seconds=3600):
        """给上海时间昨天上午种一小时有效观测，其中走动 walk_seconds。"""
        now_sh = datetime.now(SHANGHAI)
        yesterday_start = (now_sh - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        minute = yesterday_start.astimezone(ZoneInfo('UTC'))
        per_minute = max(1, walk_seconds // 60)
        with db.session_scope() as s:
            for i in range(60):
                s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-eval',
                                      minute=minute + timedelta(minutes=i), metrics='{}',
                                      actions=json.dumps({'走动': per_minute}),
                                      observed_seconds=60, frames=60))

    def _send(self, text, target='auto', request_id=None):
        response = self.client.post(self.path, headers=self.auth,
                                    json={'text': text, 'requestId': request_id or secrets.token_hex(6),
                                          'target': target})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['turn'], _latest_run(self.pet)

    def _tool_calls(self, run):
        return json.loads(run.tool_summary)

    def test_E1_data_question_uses_tool_and_source(self):
        self._seed_yesterday(walk_seconds=3600)
        turn, run = self._send('它昨天走动多吗？')
        calls = self._tool_calls(run)
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertTrue(any(c['name'] == 'query_observations' and c['allowed'] for c in calls),
                        json.dumps(calls, ensure_ascii=False))
        self.assertEqual(len(turn['sources']), 1, '来源应随回合一并交付')
        conclusion = json.loads(run.conclusion)
        self.assertEqual(conclusion['scope'], 'allowed')
        args = json.loads(next(c['arguments'] for c in calls if c['name'] == 'query_observations'))
        start = datetime.fromisoformat(args['start'])
        self.assertEqual(start.strftime('%Y-%m-%d'),
                         (datetime.now(SHANGHAI) - timedelta(days=1)).strftime('%Y-%m-%d'),
                         '「昨天」窗口应从上海昨天零点起')

    def test_E2_companion_chat_zero_tools(self):
        # 纯陪伴闲聊（不涉数据）：零工具、爱宠人格。
        # 注：「在干嘛」类问题在 W02 快照落地前查观测属合理行为，不用于本场景。
        turn, run = self._send('你今天心情怎么样呀？', target='pet')
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(self._tool_calls(run), [], '纯陪伴闲聊不应调用工具')
        self.assertEqual(json.loads(run.conclusion)['speaker'], 'pet')

    def test_E3_explicit_target_overrides(self):
        turn, run = self._send('你今天感觉怎么样？', target='pet')
        self.assertEqual(json.loads(run.conclusion)['speaker'], 'pet')
        self.assertEqual(turn['speaker'], 'pet')

    def test_E4_out_of_scope_model_refusal(self):
        turn, run = self._send('帮我用Python写一个宠物商城的完整代码')
        conclusion = json.loads(run.conclusion)
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(conclusion['scope'], 'out_of_scope', conclusion)
        self.assertEqual(self._tool_calls(run), [], '范围外不得触达业务工具')
        self.assertTrue(turn['reply'].strip())

    def test_E5_ambiguous_asks_single_question(self):
        # 无指称的模糊输入：模型应只问一个必要问题，不查数据。
        turn, run = self._send('然后呢？')
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(self._tool_calls(run), [], '无法判定主题时不应查数据')
        self.assertLessEqual(turn['reply'].count('？') + turn['reply'].count('?'), 3)

    def test_E6_write_request_honest(self):
        turn, run = self._send('记住，它的生日是2023年9月10日')
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(self._tool_calls(run), [])
        reply = turn['reply']
        self.assertFalse(any(k in reply for k in ('已为你保存', '已经保存好', '已存入档案')),
                        f'不得假称已保存：{reply}')
        self.assertTrue(any(k in reply for k in ('保存', '记忆', '档案', '记录')),
                        f'应说明保存能力状态：{reply}')

    def test_E7_search_request_honest(self):
        turn, run = self._send('帮我上网查一下今天上海限行政策')
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(self._tool_calls(run), [], '未注册搜索工具，不得调用')
        reply = turn['reply']
        self.assertFalse(any(k in reply for k in ('我查到了', '搜索结果', '根据网页')), f'不得假称联网：{reply}')
        self.assertTrue(any(k in reply for k in ('联网', '搜索', '上网', '网络')), f'应说明未联网：{reply}')

    def test_E8_today_window_boundary(self):
        self._seed_yesterday()
        turn, run = self._send('它今天走了多久？')
        calls = [c for c in self._tool_calls(run) if c['name'] == 'query_observations']
        if not calls:
            self.assertEqual(turn['status'], 'complete')
            return
        args = json.loads(calls[0]['arguments'])
        start = datetime.fromisoformat(args['start'])
        end = datetime.fromisoformat(args['end'])
        self.assertLess(start, end, '左闭右开要求 start < end')

    def test_E9_missing_data_honest(self):
        turn, run = self._send('它上周三走动多吗？')
        self.assertEqual(turn['status'], 'complete')
        calls = self._tool_calls(run)
        if not any(c['name'] == 'query_observations' and c['allowed'] for c in calls):
            self.fail('历史数据问题应调用查询工具')
        self.assertTrue(any(k in turn['reply'] for k in ('没有', '无', '未', '暂')),
                        f'数据缺失应如实说明：{turn["reply"]}')

    def test_E10_failure_preserves_owner_text(self):
        from unittest.mock import patch
        with patch('petsense.agent.model.respond', side_effect=ValueError('模拟模型不可用')):
            response = self.client.post(self.path, headers=self.auth,
                                        json={'text': '这句话要保留', 'requestId': 'e10'})
        self.assertEqual(response.json['turn']['status'], 'failed')
        self.assertEqual(response.json['turn']['text'], '这句话要保留')


if __name__ == '__main__':
    unittest.main()
