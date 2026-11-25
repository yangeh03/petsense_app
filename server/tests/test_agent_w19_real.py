"""真实模型端到端冒烟（W19）：Kimi chat 形态 + 关闭思考 + 本地搜索。

隔离临时库 + 真实 Kimi 凭据（凭据只来自环境变量，不落盘、不打印）：
1) 闲聊回合：stance 解析与正文；
2) 观测问答回合：query_observations 工具调用与来源卡；
3) 联网问答回合：search_web 工具调用、结果与网页来源卡。
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tests'))

from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import loop
from petsense.db import iso_z, utc_now
from petsense.db.models import Pet
from sqlalchemy import select


class RealKimiSmoke(unittest.TestCase):
    def setUp(self):
        if not (os.environ.get('MINIMAX_API_KEY') or os.environ.get('MINIMAX_API_KEY_FILE')):
            self.skipTest('未配置模型凭据，真实模型冒烟整组 skip')
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalars().one().id

    def _context(self, text):
        return {'pet_id': self.pet_id, 'user_id': self.uid, 'text': text, 'target': 'auto',
                'history': [], 'snapshot': {'sections': {},
                                            'meta': {'chars': 10, 'trimmed': [], 'degraded': []}},
                'now_iso': iso_z(utc_now())}

    def _run(self, label, text):
        started = time.monotonic()
        outcome = loop.run_dialogue(self._context(text))
        elapsed = time.monotonic() - started
        print('\n===== %s（%.1fs）=====' % (label, elapsed))
        print('正文:', (outcome.get('reply') or '')[:300].replace('\n', ' '))
        print('来源:', json.dumps(outcome.get('sources'), ensure_ascii=False)[:400])
        summary = outcome['trace'].get('tool_summary')
        print('工具:', json.dumps([{'name': s['name'], 'allowed': s['allowed'], 'reason': s['reason']}
                                  for s in summary], ensure_ascii=False))
        print('降级:', outcome.get('degraded'))
        return outcome

    def test_real_kimi_rounds(self):
        chat = self._run('闲聊', '你好呀，简单介绍一下你自己，一句话就行。')
        self.assertTrue((chat.get('reply') or '').strip(), '闲聊回合必须有正文')
        data = self._run('观测问答', '它昨天的活动记录有多少？用观测数据回答。')
        self.assertTrue(any(s.get('name') == 'query_observations'
                            for s in data['trace']['tool_summary']), '应调用观测工具')
        web = self._run('联网问答', '帮我搜一下今天上海的天气，并给出信息来源。')
        search_calls = [s for s in web['trace']['tool_summary'] if s['name'] == 'search_web']
        print('搜索调用:', json.dumps(search_calls, ensure_ascii=False)[:400])
        print('网页来源:', json.dumps([s for s in web.get('sources') or []
                                       if s.get('type') == 'web'], ensure_ascii=False)[:400])


if __name__ == '__main__':
    unittest.main()
