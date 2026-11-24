"""W18 群聊直派行为评测（E83–E92，承接 W17 骨架）：真实模型走 group-v1 的受理 → 派发 → 逐条生成。

覆盖 docs/agent-w17-spec.md §6 的场景表：默认由爱宠先接，内容不在爱宠范围时留一句话并转交照料助手；
只选一位严格单人；选两位都回答。断言的是「谁发言、转交后爱宠一定留了一句、权限没有被绕过、
爱宠不编数据」，不评价文采。编号从 E83 起（E80–E82 留给 W16 多会话草案）。

运行方式（本地或云端，需 MINIMAX_API_KEY(_FILE)）：
  cd server/tests && python3 -m unittest agent_behavior.test_agent_group_behavior -v
无凭据环境整组 skip（CI 默认 skip）。
"""
import hashlib
import io
import json
import os
import re
import secrets
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask  # noqa: E402
from helpers import fresh_db  # noqa: E402
from sqlalchemy import select  # noqa: E402

from petsense import config, db  # noqa: E402
from petsense.agent import group, jobs, loop  # noqa: E402
from petsense.agent import runs as runs_service  # noqa: E402
from petsense.agent.routes import bp  # noqa: E402
from petsense.db.models import AuthSession, Pet, TelemetryMinute, User  # noqa: E402
from petsense.db.models.agent import AgentRun, AgentTurn  # noqa: E402

SHANGHAI = ZoneInfo('Asia/Shanghai')
PET_TOOLS = ('read_pet_profile', 'search_memories', 'handoff_to_assistant')
# 爱宠的陪伴表达里不该出现的数据痕迹：数值与时长/距离/体征单位（「走了多少步我记不清」这类复述不算）
_DATA_TRACE = re.compile(r'\d|分钟|小时|公里|心率|电量|%|％')


def _has_credentials():
    return bool(os.environ.get('MINIMAX_API_KEY') or os.environ.get('MINIMAX_API_KEY_FILE'))


class GroupDispatchBehaviorTests(unittest.TestCase):
    """每个场景独立建库：真实模型走群聊协议整轮，断言消息行与运行轨迹。"""

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
        # 双 Agent 默认开启；裸 Flask 应用没有调度线程，用例自己推进调度
        environment = patch.dict(os.environ, {'PETSENSE_SCHEDULER_DISABLED': '',
                                              'PETSENSE_DUAL_AGENT_ENABLED': '1'})
        environment.start()
        self.addCleanup(environment.stop)  # cleanup 后进先出：它最后还原
        group.start()
        self.addCleanup(group.wait_idle, 240)  # 开关仍生效时先等后台回合结束
        media = tempfile.TemporaryDirectory()  # 图片附件落临时目录，不写运行目录
        self.addCleanup(media.cleanup)
        patched = patch.object(config, 'AGENT_MEDIA_DIR', media.name)
        patched.start()
        self.addCleanup(patched.stop)

    # -- helpers ---------------------------------------------------------
    def _seed_yesterday(self, walk_seconds=3600):
        """给上海时间昨天上午种一小时有效观测，其中走动 walk_seconds。"""
        now_sh = datetime.now(SHANGHAI)
        start = (now_sh - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        minute = start.astimezone(ZoneInfo('UTC'))
        per_minute = max(1, walk_seconds // 60)
        with db.session_scope() as s:
            for i in range(60):
                s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-eval',
                                      minute=minute + timedelta(minutes=i), metrics='{}',
                                      actions=json.dumps({'走动': per_minute}),
                                      observed_seconds=60, frames=60))

    def _send(self, text, target='auto', **fields):
        """发一条群聊协议的消息并等整轮结束，返回本轮取证（见 _evidence）。"""
        body = {'text': text, 'requestId': secrets.token_hex(6), 'target': target,
                'protocol': group.PROTOCOL, **fields}
        response = self.client.post(self.path, headers=self.auth, json=body)
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        turn_id = response.json['turn']['id']
        jobs.tick()
        self.assertTrue(group.wait_idle(240), '群聊回合没有在限时内结束')
        return self._evidence(turn_id)

    def _evidence(self, turn_id):
        """本轮取证。

        visible：客户端实际收到的消息 (role, status, text, sources)；
        rows：全部消息行 (role, status, text, id)；
        runs：按消息行 id 索引的运行摘要（分派运行不挂消息，不在此列）。
        """
        with db.session_scope() as s:
            turn = s.get(AgentTurn, turn_id)
            status, reply = turn.status, turn.reply
            view = group.turn_public(s, turn)
            rows = group.route_view(s, turn_id)['messages']
            runs = {row.message_id: runs_service.summarize(row) for row in s.execute(
                select(AgentRun).where(AgentRun.turn_id == turn_id)).scalars().all() if row.message_id}
        return {'turnId': turn_id, 'status': status, 'reply': reply,
                'visible': [(item['role'], item['status'], item['text'], item['sources'])
                            for item in view['messages']],
                'rows': [(item['role'], item['status'], item['text'], item['id']) for item in rows],
                'runs': runs}

    @staticmethod
    def _run_of(result, role):
        """某个角色的那条消息的运行摘要；本轮没有这个角色的消息行时为 None。"""
        row = next((row for row in result['rows'] if row[0] == role), None)
        return result['runs'].get(int(row[3][1:])) if row else None

    @staticmethod
    def _text_of(result, role):
        """某个角色被客户端看到的正文；这一轮它没上屏时为空串。"""
        item = next((item for item in result['visible'] if item[0] == role), None)
        return item[2] if item else ''

    @staticmethod
    def _allowed_calls(run, name):
        return [call for call in (run or {}).get('toolSummary', [])
                if call['name'] == name and call['allowed']]

    def _assert_settled(self, result):
        self.assertEqual(result['status'], 'complete', result['reply'])
        for role, status, _text, _id in result['rows']:
            self.assertEqual(status, 'complete', f'{role} 的消息没有完成：{result["reply"]}')

    def _cartoon_dog_jpeg(self):
        """「躺着」姿态的卡通狗（与图片用例同一画法）：纯图消息的评测素材现场生成。"""
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (1024, 1024), 'white')
        draw = ImageDraw.Draw(image)
        draw.ellipse([220, 620, 900, 820], fill=(230, 190, 60))
        draw.ellipse([150, 560, 400, 800], fill=(240, 200, 80))
        draw.ellipse([210, 610, 260, 660], fill='black')
        draw.polygon([(150, 560), (120, 500), (200, 530)], fill=(180, 140, 40))
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=85)
        return buffer.getvalue()

    def _upload(self, payload):
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/images', headers=self.auth,
                                    data={'image': (io.BytesIO(payload), 't.jpg', 'image/jpeg')},
                                    content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.json['id']

    # -- 场景 ------------------------------------------------------------
    def test_E83_default_companion_message_is_pet_only(self):
        result = self._send('想我了吗？')
        self._assert_settled(result)
        self.assertEqual([item[0] for item in result['visible']], ['pet'], result['visible'])
        self.assertEqual([row[0] for row in result['rows']], ['pet'], '普通闲聊不需要助手凑话')
        self.assertTrue(self._text_of(result, 'pet').strip())

    def test_E84_default_data_question_pet_leaves_a_line_then_assistant_answers(self):
        """D2（2026-11-25 负责人修订）：纯专业问题——爱宠留一句承接话并转交，照料助手随后作答。"""
        self._seed_yesterday()
        result = self._send('它今天走了多久')
        self._assert_settled(result)
        self.assertEqual([item[0] for item in result['visible']], ['pet', 'assistant'], result['visible'])
        pet_text = self._text_of(result, 'pet')
        self.assertTrue(pet_text.strip(), '转交后爱宠一定留了一句')
        self.assertIsNone(_DATA_TRACE.search(pet_text), f'爱宠的这句不报数据：{pet_text!r}')
        self.assertTrue(self._allowed_calls(self._run_of(result, 'pet'), 'handoff_to_assistant'),
                        '爱宠通过转交工具把专业部分交给照料助手')
        self.assertTrue(self._allowed_calls(self._run_of(result, 'assistant'), 'query_observations'),
                        '照料助手现场查询观测后作答')

    def test_E85_default_mixed_request_pet_companions_assistant_answers_data(self):
        self._seed_yesterday()
        result = self._send('Max，想我了吗？也帮我看看它昨天走动多不多')
        self._assert_settled(result)
        self.assertEqual([item[0] for item in result['visible']], ['pet', 'assistant'], result['visible'])
        self.assertTrue(self._text_of(result, 'pet').strip(), '爱宠接住陪伴的部分')
        self.assertTrue(self._allowed_calls(self._run_of(result, 'assistant'), 'query_observations'),
                        '照料助手回答数据部分')

    def test_E86_pet_target_professional_request_stays_with_pet(self):
        """D1：明确只选爱宠时严格单人——不触发助手，用承接句带过，不编数据。"""
        self._seed_yesterday()
        result = self._send('Max，帮我看看你昨天走了多久', target='pet')
        self._assert_settled(result)
        self.assertEqual([row[0] for row in result['rows']], ['pet'], '照料助手不参与')
        pet_text = self._text_of(result, 'pet').strip()
        self.assertIn('照料助手', pet_text, pet_text)
        self.assertIsNone(_DATA_TRACE.search(pet_text), f'爱宠不报数据：{pet_text}')
        self.assertFalse(self._allowed_calls(self._run_of(result, 'pet'), 'handoff_to_assistant'))

    def test_E87_assistant_target_with_greeting_stays_with_assistant(self):
        self._seed_yesterday()
        result = self._send('你好呀，顺便帮我看看它昨天走了多久', target='assistant')
        self._assert_settled(result)
        self.assertEqual([row[0] for row in result['rows']], ['assistant'],
                         '只点了照料助手：有寒暄也由它自己回应，爱宠不插话')
        self.assertTrue(self._text_of(result, 'assistant').strip())

    def test_E88_both_target_answers_with_two_messages(self):
        self._seed_yesterday()
        result = self._send('想我了吗？也帮我看看它昨天走了多久', target='both')
        self._assert_settled(result)
        self.assertEqual([item[0] for item in result['visible']], ['pet', 'assistant'], result['visible'])
        self.assertTrue(self._text_of(result, 'pet').strip() and self._text_of(result, 'assistant').strip())
        self.assertFalse(self._allowed_calls(self._run_of(result, 'pet'), 'handoff_to_assistant'),
                         '两位都发言时爱宠不需要转接')

    def test_E89_default_out_of_scope_is_refused_by_assistant(self):
        result = self._send('帮我用 Python 写一个快速排序')
        self._assert_settled(result)
        self.assertEqual([item[0] for item in result['visible']], ['pet', 'assistant'], result['visible'])
        self.assertTrue(self._text_of(result, 'pet').strip(), '与宠物无关的任务：爱宠留一句并转交')
        assistant = self._run_of(result, 'assistant')
        self.assertEqual(assistant['conclusion'].get('scope'), 'out_of_scope',
                         json.dumps(assistant['conclusion'], ensure_ascii=False))
        self.assertFalse([call for call in assistant['toolSummary'] if call['allowed']],
                         '范围外不执行任何业务工具')
        self.assertNotIn('def ', self._text_of(result, 'pet'), '爱宠不尝试写代码')

    def test_E90_default_image_only_pet_responds_then_professional_followup_hands_off(self):
        """D3：纯图消息由爱宠先接；随后的健康判断是专业问题，转接照料助手。"""
        image_id = self._upload(self._cartoon_dog_jpeg())
        result = self._send('', images=[image_id])
        self._assert_settled(result)
        self.assertEqual([item[0] for item in result['visible']], ['pet'], result['visible'])
        self.assertTrue(self._text_of(result, 'pet').strip())
        followup = self._send('它这样躺着一动不动，正常吗？要不要去医院？')
        self._assert_settled(followup)
        self.assertEqual([item[0] for item in followup['visible']], ['pet', 'assistant'], followup['visible'])

    def test_E91_default_quote_of_assistant_message_goes_to_assistant(self):
        self._seed_yesterday()
        first = self._send('它昨天走了多久？', target='assistant')
        self._assert_settled(first)
        second = self._send('那前天呢？', replyTo=first['turnId'])
        self._assert_settled(second)
        self.assertEqual([row[0] for row in second['rows']], ['pet', 'assistant'],
                         'W18：引用只进上下文、不改派发——爱宠先接，专业部分转交照料助手')

    def test_E92_pet_never_reports_data_when_handing_off(self):
        self._seed_yesterday()
        result = self._send('Max，今天有点想你了。它昨天走了多久？')
        self._assert_settled(result)
        pet_text = self._text_of(result, 'pet')
        self.assertIsNone(_DATA_TRACE.search(pet_text), f'爱宠转接时不编数据：{pet_text!r}')
        self.assertTrue(all(not item[3] for item in result['visible'] if item[0] == 'pet'),
                        '来源只属于真正做了查询的那条消息')
        pet_run = self._run_of(result, 'pet')
        self.assertFalse([call for call in pet_run['toolSummary']
                          if call['allowed'] and call['name'] not in PET_TOOLS], '爱宠没有越权查证')


if __name__ == '__main__':
    unittest.main()
