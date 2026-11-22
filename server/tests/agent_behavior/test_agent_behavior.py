"""W01/W02/W03 行为评测（E1–E21）：走完整 HTTP 往返 + AgentRun 轨迹断言（真实模型）。

运行方式（本地或云端，需 MINIMAX_API_KEY(_FILE)）：
  cd server && python3 -m unittest agent_behavior.test_agent_behavior -v
无凭据环境整组 skip（CI 默认 skip）。场景定义见各 test_* 方法。
"""
import hashlib
import io
import json
import os
import secrets
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from helpers import fresh_db

from petsense import config, db
from petsense.agent.routes import bp
from petsense.db import utc_now
from petsense.db.models import AuthSession, Pet, TelemetryEvent, TelemetryLatest, TelemetryMinute, User
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
        media = tempfile.TemporaryDirectory()  # W07：图片附件落临时目录，不写运行目录
        self.addCleanup(media.cleanup)
        patched = patch.object(config, 'AGENT_MEDIA_DIR', media.name)
        patched.start()
        self.addCleanup(patched.stop)

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

    def _seed_latest(self, action='walking', battery=76, age_seconds=30):
        """种一帧新鲜 TelemetryLatest（W02 快照与工具的实时来源）。"""
        payload = {'source': 'esp32_mlp', 'battery': {'percent': battery},
                   'health': {'heartRate': 120, 'spo2': 97, 'bodyTemp': 38.5, 'valid': True},
                   'action': {'label': action, 'confidence': 0.9}}
        with db.session_scope() as s:
            s.add(TelemetryLatest(device_id='esp32-eval', owner_id=self.uid, boot_id='b1', ordinal=1,
                                  received_at=utc_now() - timedelta(seconds=age_seconds),
                                  payload=json.dumps(payload)))

    def _send(self, text, target='auto', request_id=None, images=None):
        body = {'text': text, 'requestId': request_id or secrets.token_hex(6), 'target': target}
        if images:
            body['images'] = images
        response = self.client.post(self.path, headers=self.auth, json=body)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['turn'], _latest_run(self.pet)

    def _make_image(self, kind):
        """PIL 生成可断言内容的测试图（W07：评测素材现场生成，不落仓库二进制资产）。"""
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (1024, 1024), 'white')
        draw = ImageDraw.Draw(image)
        font = None
        if kind != 'pet':
            try:
                font = ImageFont.load_default(size=64)
            except TypeError:
                font = None
        if kind == 'label':
            draw.text((120, 100), 'PETSENSE 7319', fill='black', font=font)
            draw.ellipse([110, 420, 430, 740], fill=(220, 30, 30))
            draw.polygon([(600, 740), (900, 740), (750, 440)], fill=(30, 60, 220))
        elif kind == 'pet':
            # 「躺着」姿态的卡通狗：身体横卧、头贴地
            draw.ellipse([220, 620, 900, 820], fill=(230, 190, 60))
            draw.ellipse([150, 560, 400, 800], fill=(240, 200, 80))
            draw.ellipse([210, 610, 260, 660], fill='black')
            draw.polygon([(150, 560), (120, 500), (200, 530)], fill=(180, 140, 40))
        elif kind == 'code':
            draw.text((80, 100), 'def feed_pet():', fill='black', font=font)
            draw.text((80, 200), '    return 42', fill='black', font=font)
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=85)
        return buffer.getvalue(), font is not None

    def _upload_image(self, payload, name='t.jpg'):
        response = self.client.post(f'/api/app/agent/pets/{self.pet}/images', headers=self.auth,
                                    data={'image': (io.BytesIO(payload), name, 'image/jpeg')},
                                    content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.json['id']

    def _tool_calls(self, run):
        return json.loads(run.tool_summary)

    @staticmethod
    def _arguments(call):
        """轨迹里的 arguments 是模型原始输出，偶发非法转义（loop 已按 invalid 参数拒绝）；
        测试侧容错解析，避免模型输出毛刺造成评测假阴性。"""
        try:
            return json.loads(call.get('arguments') or '{}')
        except ValueError:
            return {}

    def test_E1_data_question_uses_tool_and_source(self):
        self._seed_yesterday(walk_seconds=3600)
        turn, run = self._send('它昨天走动多吗？')
        calls = self._tool_calls(run)
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertTrue(any(c['name'] == 'query_observations' and c['allowed'] for c in calls),
                        json.dumps(calls, ensure_ascii=False))
        self.assertGreaterEqual(len(turn['sources']), 1, '来源应随回合一并交付')
        conclusion = json.loads(run.conclusion)
        self.assertEqual(conclusion['scope'], 'allowed')
        args = self._arguments(next(c for c in calls if c['name'] == 'query_observations'))
        start = datetime.fromisoformat(args['start'])
        self.assertEqual(start.strftime('%Y-%m-%d'),
                         (datetime.now(SHANGHAI) - timedelta(days=1)).strftime('%Y-%m-%d'),
                         '「昨天」窗口应从上海昨天零点起')

    def test_E2_companion_chat_pet_persona(self):
        # 陪伴闲聊：爱宠人格接话；工具仅限事实只读组且 ≤4 次（2026-11-22 负责人确认：
        # 不收紧「零工具」断言——快照提供事实，直答还是现查由模型判断，W02 spec §2。
        # W03 起事实只读组另含 read_pet_profile）。
        turn, run = self._send('你今天心情怎么样呀？', target='pet')
        self.assertEqual(turn['status'], 'complete')
        calls = self._tool_calls(run)
        self.assertTrue(all(c['name'] in ('query_observations', 'read_current_status', 'read_baseline', 'read_pet_profile') for c in calls))
        self.assertLessEqual(len(calls), 4, '陪伴闲聊不应连环查数据')
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

    def test_E5_ambiguous_delivers_single_question(self):
        # 无指称的模糊输入：交付一个必要问题即可；不设「零工具」断言（2026-11-22
        # 负责人确认维持 W01 校准口径，W02 spec §2；W03 起只读组另含 read_pet_profile）。
        turn, run = self._send('然后呢？')
        self.assertEqual(turn['status'], 'complete')
        calls = self._tool_calls(run)
        self.assertTrue(all(c['name'] in ('query_observations', 'read_current_status', 'read_baseline', 'read_pet_profile') for c in calls))
        self.assertGreaterEqual(turn['reply'].count('？') + turn['reply'].count('?'), 1,
                                '模糊输入的交付应包含一个澄清问题')
        self.assertLessEqual(turn['reply'].count('？') + turn['reply'].count('?'), 3)

    def test_E6_explicit_write_direct_path(self):
        # W03 反转（agent-w03-spec.md §6）：明确指令直写并回显；旧「能力未开放」断言废止。
        turn, run = self._send('记住，它的生日是2023年9月10日')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        used = next((c for c in calls if c['name'] == 'prepare_profile_update' and c['allowed']), None)
        self.assertIsNotNone(used, json.dumps(calls, ensure_ascii=False))
        self.assertTrue(self._arguments(used).get('explicit'),
                        '明确「记住」指令应走直写路径')
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            self.assertEqual(pet.birth_date.strftime('%Y-%m-%d'), '2023-09-10')
            self.assertEqual(pet.birth_date_precision, 'day')
        reply = turn['reply']
        self.assertRegex(reply, r'(?:已(?:经|成功)?\s*(?:记下|记好|记录|保存|写入|更新)|(?:记下|记好|保存)了)',
                         f'应确认写入成功：{reply}')
        self.assertRegex(reply, r'2023\s*(?:年|-)\s*0?9\s*(?:月|-)\s*10',
                         f'应回显确认写入的日期：{reply}')
        self.assertFalse(any(k in reply for k in ('还不能保存', '未开放', '暂时无法保存')),
                        f'写入已开放，不得再称能力未接入：{reply}')

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
        args = self._arguments(calls[0])
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

    def test_E11_current_activity_consistent(self):
        # W02：实时状态问题——快照氛围行含「走动」，直答或现查均可，答案与事实一致。
        self._seed_latest(action='walking', battery=76)
        turn, run = self._send('它现在在干什么？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertEqual(json.loads(run.conclusion)['scope'], 'allowed')
        calls = self._tool_calls(run)
        self.assertTrue(all(c['name'] in ('query_observations', 'read_current_status', 'read_baseline') for c in calls),
                        json.dumps(calls, ensure_ascii=False))
        snapshot = json.loads(run.snapshot)['sections'] if run.snapshot else {}
        self.assertEqual(snapshot.get('current', {}).get('action'), '走动', '快照应含当前动作')
        reply = turn['reply']
        if snapshot.get('current', {}).get('action') == '走动':
            # 检查宣称性表述而非裸词（W02 E14 同口径：如实枚举「躺卧/站/坐」这类姿态名不算编造）
            for claim in ('正在躺着', '正在趴着', '正在睡觉', '在睡觉', '躺着呢', '睡著了', '睡着了'):
                self.assertNotIn(claim, reply, f'快照为走动，不得宣称相反姿态：{claim}')

    def test_E12_battery_value_traceable(self):
        # W02：电量数值可溯源（快照 batteryPercent=76 或本次工具结果）。
        self._seed_latest(action='lying down', battery=76)
        turn, run = self._send('项圈还有电吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        snapshot = json.loads(run.snapshot)['sections'] if run.snapshot else {}
        self.assertEqual(snapshot.get('current', {}).get('batteryPercent'), 76)
        self.assertIn('76', turn['reply'], f'电量数值应可溯源到快照：{turn["reply"]}')

    def test_E13_today_walk_same_caliber(self):
        # W02：今日走动——经 read_current_status today 节或 query_observations 当日窗口，同口径。
        self._seed_latest(action='walking')
        now_sh = datetime.now(SHANGHAI)
        today_start = now_sh.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo('UTC'))
        with db.session_scope() as s:
            for i in range(30):
                s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-eval',
                                      minute=today_start + timedelta(hours=9, minutes=i), metrics='{}',
                                      actions=json.dumps({'走动': 30}), observed_seconds=60, frames=60))
        turn, run = self._send('它今天走了多久？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        used = [c for c in calls if c['name'] in ('read_current_status', 'query_observations') and c['allowed']]
        self.assertTrue(used, json.dumps(calls, ensure_ascii=False))

    def test_E14_unbound_device_honest(self):
        # W02：未绑定设备——快照与工具如实 unbound，不编造状态。
        with db.session_scope() as s:
            s.get(Pet, self.pet).device_id = ''
        turn, run = self._send('它现在在干什么？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        snapshot = json.loads(run.snapshot)['sections'] if run.snapshot else {}
        self.assertEqual(snapshot.get('current'), {'state': 'unbound'})
        self.assertTrue(any(k in turn['reply'] for k in
                            ('还没绑定', '未绑定', '没有绑定', '还没', '暂无', '没有数据', '暂时无法', '离线')),
                        f'未绑定设备应如实说明：{turn["reply"]}')
        # 不得「宣称」当前姿态（枚举「无法判断走动还是躺卧」这类如实表达不算）
        for claim in ('正在走动', '正在散步', '正在睡觉', '正在躺着', '正在趴着', '在跑步'):
            self.assertNotIn(claim, turn['reply'], f'未绑定设备不得编造姿态：{claim}')

    def test_E15_recent_sounds_deep_query(self):
        # W02：最近一小时声音——调 read_current_status 深查，窗口按分钟回看。
        self._seed_latest(action='standing')
        turn, run = self._send('最近一小时它有什么声音吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        used = next((c for c in calls if c['name'] == 'read_current_status' and c['allowed']), None)
        if used is None:
            self.assertTrue(any(c['name'] == 'query_observations' and c['allowed'] for c in calls),
                            json.dumps(calls, ensure_ascii=False))
        else:
            minutes = self._arguments(used).get('minutes', 30)
            self.assertTrue(30 <= minutes <= 120, f'回看窗口应覆盖「最近一小时」：{minutes}')

    # ---- W03：资料确认协议与助手设定（agent-w03-spec.md §6）----

    def _proposal_states(self):
        from petsense.db.models.agent import AgentProposal
        with db.session_scope() as s:
            rows = s.execute(select(AgentProposal).where(AgentProposal.pet_id == self.pet)).scalars().all()
            return [(r.status, r.version) for r in rows]

    def test_E16_fuzzy_age_proposes_pending_card(self):
        # 模糊年龄：生成 pending 候选（估算精度），不写档案、不自称已保存。
        turn, run = self._send('它其实已经三岁了')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        used = [c for c in calls if c['name'] == 'prepare_profile_update' and c['allowed']]
        self.assertTrue(used, json.dumps(calls, ensure_ascii=False))
        self.assertFalse(self._arguments(used[0]).get('explicit'), '模糊输入不得走直写')
        states = self._proposal_states()
        self.assertIn('pending', [s for s, _ in states], f'应生成待确认提案：{states}')
        with db.session_scope() as s:
            self.assertIsNone(s.get(Pet, self.pet).birth_date, '未确认不得写档案')
        reply = turn['reply']
        self.assertFalse(any(k in reply for k in ('已保存', '已写入档案', '档案已更新', '已更新档案')),
                        f'未经确认不得自称已保存：{reply}')
        card = [b for b in (turn['blocks'] or []) if b.get('type') == 'proposal']
        self.assertEqual(len(card), 1, '候选应挂确认卡')
        self.assertEqual(card[0]['proposal']['fields'].get('birthDatePrecision'), 'year')

    def test_E17_same_day_weight_direct_write(self):
        # 当日体重：明确「帮我记一下」直写，按日记录并保留原始表述。
        turn, run = self._send('今天称了一下，它4.5公斤，帮我记一下')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        used = [c for c in calls if c['name'] == 'prepare_profile_update' and c['allowed']]
        self.assertTrue(used, json.dumps(calls, ensure_ascii=False))
        self.assertTrue(self._arguments(used[0]).get('explicit'))
        from petsense.db.models import PetWeightRecord
        with db.session_scope() as s:
            record = s.execute(select(PetWeightRecord).where(
                PetWeightRecord.pet_id == self.pet)).scalar_one()
            self.assertEqual(record.weight_g, 4500)
            self.assertEqual(record.measured_on.strftime('%Y-%m-%d'),
                             datetime.now(SHANGHAI).strftime('%Y-%m-%d'), '发生日期应为上海今天')
        self.assertIn('4.5', turn['reply'], f'回显数值应与写入一致：{turn["reply"]}')

    def test_E18_other_subject_not_written(self):
        # 其他主体：不调用写入工具、不建提案、档案不变。
        turn, run = self._send('朋友家的狗今年五岁了，好快呀')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        self.assertFalse(any(c['name'] == 'prepare_profile_update' for c in calls),
                         json.dumps(calls, ensure_ascii=False))
        self.assertEqual(self._proposal_states(), [])
        with db.session_scope() as s:
            self.assertIsNone(s.get(Pet, self.pet).birth_date)

    def test_E19_hypothesis_not_written(self):
        # 假设：可正常回答话题，但不写入。
        turn, run = self._send('假如它三岁了，饮食上要注意什么？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        self.assertFalse(any(c['name'] == 'prepare_profile_update' for c in calls),
                         json.dumps(calls, ensure_ascii=False))
        self.assertEqual(self._proposal_states(), [])
        with db.session_scope() as s:
            self.assertIsNone(s.get(Pet, self.pet).birth_date)

    def test_E20_role_settings_direct_write(self):
        # 设定修改：明确指令直写，设定落库并回显。
        turn, run = self._send('以后就叫你小禾吧')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        used = [c for c in calls if c['name'] == 'prepare_role_settings' and c['allowed']]
        self.assertTrue(used, json.dumps(calls, ensure_ascii=False))
        from petsense.db.models.agent import CompanionSettings
        with db.session_scope() as s:
            row = s.get(CompanionSettings, self.uid)
            self.assertIsNotNone(row)
            self.assertEqual(row.assistant_name, '小禾')
        self.assertIn('小禾', turn['reply'])

    def test_E21_read_profile_answers_age(self):
        # 读档案：工具取数，数值与档案一致。
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            pet.birth_date = datetime.strptime('2023-09-10', '%Y-%m-%d').date()
            pet.birth_date_precision = 'day'
        turn, run = self._send('它现在多大了？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        used = [c for c in calls if c['name'] == 'read_pet_profile' and c['allowed']]
        self.assertTrue(used, json.dumps(calls, ensure_ascii=False))
        age_now = datetime.now(SHANGHAI).year - 2023
        numerals = '一二三四五六七八九十'
        self.assertTrue(f'{age_now} 岁' in turn['reply'] or f'{age_now}岁' in turn['reply']
                        or (0 <= age_now < 10 and f'{numerals[age_now]}岁' in turn['reply']),
                        f'年龄应与档案推导一致（{age_now} 岁）：{turn["reply"]}')


    # ---- W05：个人基线（agent-w05-spec.md §6，E22–E26） ----

    def _seed_ready_days(self, count=8, sounds_days=0, today_walk=None, today_events=0):
        """种就绪基线：count 个有效日（上午走 30s/分、下午 20s/分）；sounds_days 控制声音有效日数。

        分钟行同时带 actions/sounds 版本标注（sound_observed_seconds 同步累计），
        声音维度是否就绪由 AgentLearningDay 的 sounds 行数决定（分维度独立，E26）。
        """
        from petsense.db.models.agent import AgentLearningDay
        now_sh = datetime.now(SHANGHAI)
        versions = ['actions:v1'] + (['sounds:v1'] if sounds_days or today_events else [])
        with db.session_scope() as s:
            for offset in range(1, count + 1):
                day = now_sh - timedelta(days=offset)
                midnight = day.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo('UTC'))
                for slot, walk in ((1, 30), (2, 20)):
                    start = midnight + timedelta(hours=6 * slot)
                    for i in range(70):
                        s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-eval',
                                              minute=start + timedelta(minutes=i), metrics='{}',
                                              actions=json.dumps({'走动': walk}),
                                              observed_seconds=60, sound_observed_seconds=60,
                                              source_versions=json.dumps(versions)))
                s.add(AgentLearningDay(pet_id=self.pet, device_id='esp32-eval', feature='actions',
                                       version='v1', day=day.date().isoformat(), seconds=21600,
                                       last_seen_at=db.utc_now(), slots='[1,2]'))
                if offset <= sounds_days:
                    s.add(AgentLearningDay(pet_id=self.pet, device_id='esp32-eval', feature='sounds',
                                           version='v1', day=day.date().isoformat(), seconds=21600,
                                           last_seen_at=db.utc_now(), slots='[1,2]'))
                if today_events and offset == 1:
                    for i in range(2):
                        moment = midnight + timedelta(hours=13, minutes=i)
                        s.add(TelemetryEvent(owner_id=self.uid, device_id='esp32-eval', boot_id='b1',
                                             model_version='v1', label='bark', started_at=moment,
                                             last_seen_at=moment + timedelta(seconds=5), frames=1,
                                             confidence_sum=0.9, observed_seconds=5))
            if today_walk is not None:
                midnight = now_sh.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo('UTC'))
                for i in range(70):
                    s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-eval',
                                          minute=midnight + timedelta(hours=9, minutes=i), metrics='{}',
                                          actions=json.dumps({'走动': today_walk}),
                                          observed_seconds=60, sound_observed_seconds=60,
                                          source_versions=json.dumps(versions)))
                for i in range(today_events):
                    moment = midnight + timedelta(hours=9, minutes=i)
                    s.add(TelemetryEvent(owner_id=self.uid, device_id='esp32-eval', boot_id='b1',
                                         model_version='v1', label='bark', started_at=moment,
                                         last_seen_at=moment + timedelta(seconds=5), frames=1,
                                         confidence_sum=0.9, observed_seconds=5))

    def test_E22_learning_no_fabricated_comparison(self):
        # 认识中：不说「比平时」的结论，给学习状态或当天事实；不编造个人参照范围。
        self._seed_latest(action='walking')
        turn, run = self._send('它今天走得比平时多吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertEqual(json.loads(run.conclusion)['scope'], 'allowed')
        reply = turn['reply']
        self.assertTrue(any(k in reply for k in
                            ('认识', '学习', '积累', '有效日', '还需要', '暂时', '还不', '无法', '不能', '没有足够')),
                        f'认识中应说明学习状态而不是直接下结论：{reply}')
        for claim in ('明显比平时多', '明显比平时少', '比平时多得多', '比平时少得多', '高于平时范围', '低于平时范围'):
            self.assertNotIn(claim, reply, f'认识中不得下基线比较结论：{claim}')

    def test_E23_ready_comparison_uses_baseline_tool(self):
        # 已就绪：比平时类问题调 read_baseline，回答数值可溯源到工具结果。
        self._seed_latest(action='walking')
        self._seed_ready_days(count=8, today_walk=60)
        turn, run = self._send('它今天走得比平时多吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        self.assertTrue(any(c['name'] == 'read_baseline' and c['allowed'] for c in calls),
                        json.dumps(calls, ensure_ascii=False))
        reply = turn['reply']
        self.assertTrue(any(char.isdigit() for char in reply), f'比较类回答应含具体数值：{reply}')
        snapshot = json.loads(run.snapshot)['sections'] if run.snapshot else {}
        self.assertIn('已就绪', snapshot.get('learning', ''), '快照学习行应显示动作已就绪')

    def test_E24_insufficient_coverage_not_reduction(self):
        # 当天数据不足：不得把覆盖不足当作活动减少，如实说明数据情况。
        self._seed_latest(action='standing')
        self._seed_ready_days(count=8)
        turn, run = self._send('今天下午它是不是活动减少了？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        self.assertTrue(all(c['name'] in ('query_observations', 'read_current_status', 'read_baseline')
                            for c in calls), json.dumps(calls, ensure_ascii=False))
        reply = turn['reply']
        self.assertTrue(any(k in reply for k in ('没有', '无', '未', '缺', '不足', '暂')),
                        f'覆盖不足应如实说明，而不是断言活动减少：{reply}')

    def test_E25_typical_range_from_tool(self):
        # 「平时一般走多久」：引用参照范围与样本，不把当天值混进「平时」。
        self._seed_latest(action='standing')
        self._seed_ready_days(count=8)
        turn, run = self._send('它平时下午一般走多久？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = self._tool_calls(run)
        self.assertTrue(any(c['name'] == 'read_baseline' and c['allowed'] for c in calls),
                        json.dumps(calls, ensure_ascii=False))
        reply = turn['reply']
        self.assertIn('分钟', reply, f'参照范围应以分钟表达：{reply}')
        self.assertTrue(any(char.isdigit() for char in reply))

    def test_E26_dimension_independence(self):
        # 动作已就绪、声音认识中：声音不给个人比较，如实说明学习状态。
        self._seed_latest(action='standing')
        self._seed_ready_days(count=8, sounds_days=2, today_events=3)
        turn, run = self._send('它今天叫声比平时多吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        reply = turn['reply']
        self.assertTrue(any(k in reply for k in
                            ('认识', '学习', '积累', '有效日', '还需要', '暂时', '还不', '无法', '不能')),
                        f'声音认识中应说明学习状态：{reply}')

    # ---- W04：长期记忆与分级写入（agent-w04-spec.md §6，E27–E34） ----

    def _write_calls(self, run):
        return [c for c in self._tool_calls(run)
                if c['name'] == 'write_memory' and c['allowed']]

    def _memory_rows(self, kind=None):
        from petsense.db.models.memory import PetMemory
        with db.session_scope() as s:
            stmt = select(PetMemory).where(PetMemory.pet_id == self.pet)
            if kind:
                stmt = stmt.where(PetMemory.kind == kind)
            return s.execute(stmt.order_by(PetMemory.id)).scalars().all()

    def _seed_episode(self, summary, day_offset=1):
        from petsense.agent import memory as memory_store
        from petsense.db.models.memory import PetMemory
        start = (datetime.now(SHANGHAI) - timedelta(days=day_offset)).replace(
            hour=15, minute=0, second=0, microsecond=0)
        with db.session_scope() as s:
            entry, error = memory_store.save_entry(
                s, self.pet, kind='episode', summary=summary,
                start_at=start.astimezone(ZoneInfo('UTC')), end_at=None)
            assert error is None, error
            return entry.id

    def test_E27_experience_autonomous_write(self):
        # 主人讲述经历 → write_memory(episode) 自主写、轻量回显；不写偏好/习惯候选。
        turn, run = self._send('今天下午带它去公园玩了飞盘，它玩得特别开心')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        writes = self._write_calls(run)
        self.assertTrue(writes, '主人讲述经历应触发 write_memory（轨迹可证）')
        args = self._arguments(writes[0])
        self.assertEqual(args.get('kind'), 'episode', json.dumps(writes, ensure_ascii=False))
        rows = self._memory_rows()
        self.assertTrue(any(r.kind == 'episode' and r.status == 'active' for r in rows))
        self.assertFalse(any(r.kind in ('preference', 'habit_candidate', 'daily') for r in rows),
                        '讲述经历不应写偏好/习惯候选/汇总')
        self.assertNotIn('确认', turn['reply'], '自主写不打断对话，无确认卡语义')

    def test_E28_recall_across_days(self):
        # 跨天找回：昨天种一条经历，本轮无历史窗口，问「上次」必须检索而不是编造。
        yesterday = (datetime.now(SHANGHAI) - timedelta(days=1)).strftime('%Y-%m-%d')
        self._seed_episode('带它去公园玩了飞盘，它玩得特别开心')
        turn, run = self._send('我上次说去公园是什么时候来着？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = [c for c in self._tool_calls(run) if c['name'] == 'search_memories' and c['allowed']]
        self.assertTrue(calls, '找回过去的事必须先 search_memories（轨迹可证）')
        self.assertIn('公园', turn['reply'])
        self.assertTrue(any((k in turn['reply']) for k in (yesterday, '昨天', '前一天')),
                        f'日期应来自记忆条目：{turn["reply"]}')

    def test_E29_fuzzy_keyword_recall(self):
        # 模糊关键词（「上次那件半夜叫的事」）：FTS5 关键词召回。
        self._seed_episode('上周三半夜狗叫了好久，后来发现是隔壁装修')
        turn, run = self._send('上次那件半夜叫的事是什么情况来着？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = [c for c in self._tool_calls(run) if c['name'] == 'search_memories' and c['allowed']]
        self.assertTrue(calls, json.dumps(self._tool_calls(run), ensure_ascii=False))
        args = self._arguments(calls[0])
        self.assertTrue(args.get('keyword'), '模糊指称应带关键词检索')
        self.assertTrue(any(k in turn['reply'] for k in ('叫', '装修', '隔壁')),
                        f'应引用记忆内容：{turn["reply"]}')

    def test_E30_no_write_boundaries(self):
        # 假设、他宠、偏好：一律不写（轨迹可证）；偏好如实说明确认保存未接入。
        turn, run = self._send('假如它三岁了，一天要睡多久？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertEqual(self._write_calls(run), [], '假设不写入')
        turn, run = self._send('朋友家的狗今年五岁了，帮我记住')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertEqual(self._write_calls(run), [], '其他主体不写入')
        turn, run = self._send('它好像不喜欢吃鸡肉，帮我记住这个')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        writes = self._write_calls(run)
        self.assertEqual(writes, [], '长期偏好属确认写，本包不自主写入')
        self.assertTrue(any(k in turn['reply'] for k in ('确认', '不能', '暂时', '还无法', '尚未')),
                        f'偏好应如实说明确认保存未接入：{turn["reply"]}')
        self.assertEqual(self._memory_rows(), [], '三类内容都不应落库')

    def test_E31_correction_supersedes(self):
        # 纠正：「记错了」→ supersedesId 新版本，旧条目 superseded、版本链完整。
        turn, run = self._send('记住：它上次洗澡是上周六')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertTrue(self._write_calls(run), '先正确写入一条')
        first = self._memory_rows('episode')[0]
        turn, run = self._send('刚才记错了，它上次洗澡其实是上周日')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        writes = self._write_calls(run)
        self.assertTrue(writes, '纠正应再次写入')
        args = self._arguments(writes[-1])
        self.assertTrue(args.get('supersedesId') or args.get('forget'),
                        f'纠正应引用旧条目：{json.dumps(writes, ensure_ascii=False)}')
        rows = self._memory_rows('episode')
        active = [r for r in rows if r.status == 'active']
        self.assertEqual(len(active), 1, '纠正后只有一条当前有效版本')
        self.assertEqual(active[0].version, 2, '版本链应递增')
        self.assertTrue(any(r.status in ('superseded', 'retired') for r in rows), '旧版本保留可追溯')

    def test_E32_write_boundaries_chitchat_vs_dense(self):
        # 记少：无信息量闲聊零写入；记多上限：高密度讲述 ≤3 条。
        turn, run = self._send('在吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertEqual(self._write_calls(run), [], '闲聊不写记忆')
        turn, run = self._send(
            '今天去了趟宠物医院，医生说它耳朵有点发炎给开了滴耳液；'
            '顺路买了新的鸡肉冻干它超爱；护士提醒下周三要复诊')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        writes = self._write_calls(run)
        self.assertTrue(writes, '高密度讲述应至少记一条')
        self.assertLessEqual(len(self._memory_rows()), 3, '每回合写入上限 3 条')

    def test_E33_empty_store_honest(self):
        # 空库诚实：无记忆时不编造。
        turn, run = self._send('你还记得我上次跟你说的事吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = [c for c in self._tool_calls(run) if c['name'] == 'search_memories' and c['allowed']]
        self.assertTrue(calls, '应先检索记忆')
        self.assertTrue(any(k in turn['reply'] for k in ('没有', '还没', '不记得', '没记住', '暂时没有')),
                        f'空库应如实说明：{turn["reply"]}')

    def test_E34_forget_on_request(self):
        # 忘掉：主人明确要求 → forget_memory，条目 retired。
        turn, run = self._send('记住：它下周四要吃驱虫药')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertTrue(self._write_calls(run), '先写入一条')
        memory_id = self._memory_rows()[0].id
        turn, run = self._send('把刚才记的那条忘掉吧')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = [c for c in self._tool_calls(run) if c['name'] == 'forget_memory' and c['allowed']]
        self.assertTrue(calls, '明确要求忘掉应调用 forget_memory')
        rows = self._memory_rows()
        self.assertTrue(all(r.status == 'retired' for r in rows), '条目应 retired 且保留可审计')

    def test_E39_image_understanding(self):
        # W07：真实图片进对话运行——描述与图一致、不添加图中没有的细节。
        payload, has_font = self._make_image('label')
        image_id = self._upload_image(payload)
        turn, run = self._send('看看这张图，图里有什么？', images=[image_id])
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertEqual(json.loads(run.conclusion)['scope'], 'allowed')
        self.assertIn('红', turn['reply'])
        self.assertIn('蓝', turn['reply'])
        if has_font:
            self.assertIn('7319', turn['reply'], f'图中文字应被正确读出：{turn["reply"]}')
        self.assertNotIn('绿色', turn['reply'], '不得编造图中不存在的颜色')

    def test_E40_photo_is_not_live_state(self):
        # W07 核心边界：单张照片≠持续观测——照片是躺着的狗，实时种子是走动；
        # 「它现在在干什么」必须以快照/工具为准，不得把照片当当前状态。
        payload, _ = self._make_image('pet')
        image_id = self._upload_image(payload)
        self._seed_latest(action='walking')
        turn, run = self._send('它现在在干什么？', images=[image_id])
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertIn('走动', turn['reply'], f'当前状态应来自快照/实时工具（种子=走动）：{turn["reply"]}')
        for claim in ('正在躺着', '正在趴着', '正在睡觉'):
            self.assertNotIn(claim, turn['reply'], f'不得把照片姿态当作当前状态：{claim}')

    def test_E41_image_does_not_widen_scope(self):
        # W07：图片不扩大话题范围——编程请求即使带截图仍 out_of_scope，工具被矩阵拦截。
        payload, _ = self._make_image('code')
        image_id = self._upload_image(payload)
        turn, run = self._send('把图里这段代码补全并解释每一行', images=[image_id])
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertEqual(json.loads(run.conclusion)['scope'], 'out_of_scope',
                         f'编程任务应被拒（即使带图）：{turn["reply"]}')
        for call in self._tool_calls(run):
            self.assertFalse(call['allowed'], 'out_of_scope 下业务工具必须被矩阵拦截')


if __name__ == '__main__':
    unittest.main()
