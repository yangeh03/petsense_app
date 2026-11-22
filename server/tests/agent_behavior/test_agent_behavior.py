"""W01/W02 行为评测（E1–E15）：走完整 HTTP 往返 + AgentRun 轨迹断言（真实模型）。

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
        self.assertGreaterEqual(len(turn['sources']), 1, '来源应随回合一并交付')
        conclusion = json.loads(run.conclusion)
        self.assertEqual(conclusion['scope'], 'allowed')
        args = json.loads(next(c['arguments'] for c in calls if c['name'] == 'query_observations'))
        start = datetime.fromisoformat(args['start'])
        self.assertEqual(start.strftime('%Y-%m-%d'),
                         (datetime.now(SHANGHAI) - timedelta(days=1)).strftime('%Y-%m-%d'),
                         '「昨天」窗口应从上海昨天零点起')

    def test_E2_companion_chat_pet_persona(self):
        # 陪伴闲聊：爱宠人格接话；工具仅限事实只读组且 ≤4 次（2026-11-22 负责人确认：
        # 不收紧「零工具」断言——快照提供事实，直答还是现查由模型判断，W02 spec §2）。
        turn, run = self._send('你今天心情怎么样呀？', target='pet')
        self.assertEqual(turn['status'], 'complete')
        calls = self._tool_calls(run)
        self.assertTrue(all(c['name'] in ('query_observations', 'read_current_status', 'read_baseline') for c in calls))
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
        # 负责人确认维持 W01 校准口径，W02 spec §2）。
        turn, run = self._send('然后呢？')
        self.assertEqual(turn['status'], 'complete')
        calls = self._tool_calls(run)
        self.assertTrue(all(c['name'] in ('query_observations', 'read_current_status', 'read_baseline') for c in calls))
        self.assertGreaterEqual(turn['reply'].count('？') + turn['reply'].count('?'), 1,
                                '模糊输入的交付应包含一个澄清问题')
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
            self.assertNotIn('躺卧', reply.replace('不是', '').replace('没在', ''))
            self.assertNotIn('睡觉', reply.replace('不是', '').replace('没在', ''))

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
            minutes = json.loads(used['arguments']).get('minutes', 30)
            self.assertTrue(30 <= minutes <= 120, f'回看窗口应覆盖「最近一小时」：{minutes}')

    # ---- W05：个人基线（agent-w05-spec.md §6，E16–E20） ----

    def _seed_ready_days(self, count=8, sounds_days=0, today_walk=None, today_events=0):
        """种就绪基线：count 个有效日（上午走 30s/分、下午 20s/分）；sounds_days 控制声音有效日数。

        分钟行同时带 actions/sounds 版本标注（sound_observed_seconds 同步累计），
        声音维度是否就绪由 AgentLearningDay 的 sounds 行数决定（分维度独立，E20）。
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

    def test_E16_learning_no_fabricated_comparison(self):
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

    def test_E17_ready_comparison_uses_baseline_tool(self):
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

    def test_E18_insufficient_coverage_not_reduction(self):
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

    def test_E19_typical_range_from_tool(self):
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

    def test_E20_dimension_independence(self):
        # 动作已就绪、声音认识中：声音不给个人比较，如实说明学习状态。
        self._seed_latest(action='standing')
        self._seed_ready_days(count=8, sounds_days=2, today_events=3)
        turn, run = self._send('它今天叫声比平时多吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        reply = turn['reply']
        self.assertTrue(any(k in reply for k in
                            ('认识', '学习', '积累', '有效日', '还需要', '暂时', '还不', '无法', '不能')),
                        f'声音认识中应说明学习状态：{reply}')

    # ---- W04：长期记忆与分级写入（agent-w04-spec.md §6，E21–E28） ----

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

    def test_E21_experience_autonomous_write(self):
        # 主人讲述经历 → write_memory(episode) 自主写、轻量回显；不写偏好/习惯候选。
        turn, run = self._send('今天下午带它去公园玩了飞盘，它玩得特别开心')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        writes = self._write_calls(run)
        self.assertTrue(writes, '主人讲述经历应触发 write_memory（轨迹可证）')
        args = json.loads(writes[0]['arguments'])
        self.assertEqual(args.get('kind'), 'episode', json.dumps(writes, ensure_ascii=False))
        rows = self._memory_rows()
        self.assertTrue(any(r.kind == 'episode' and r.status == 'active' for r in rows))
        self.assertFalse(any(r.kind in ('preference', 'habit_candidate', 'daily') for r in rows),
                        '讲述经历不应写偏好/习惯候选/汇总')
        self.assertNotIn('确认', turn['reply'], '自主写不打断对话，无确认卡语义')

    def test_E22_recall_across_days(self):
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

    def test_E23_fuzzy_keyword_recall(self):
        # 模糊关键词（「上次那件半夜叫的事」）：FTS5 关键词召回。
        self._seed_episode('上周三半夜狗叫了好久，后来发现是隔壁装修')
        turn, run = self._send('上次那件半夜叫的事是什么情况来着？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = [c for c in self._tool_calls(run) if c['name'] == 'search_memories' and c['allowed']]
        self.assertTrue(calls, json.dumps(self._tool_calls(run), ensure_ascii=False))
        args = json.loads(calls[0]['arguments'])
        self.assertTrue(args.get('keyword'), '模糊指称应带关键词检索')
        self.assertTrue(any(k in turn['reply'] for k in ('叫', '装修', '隔壁')),
                        f'应引用记忆内容：{turn["reply"]}')

    def test_E24_no_write_boundaries(self):
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

    def test_E25_correction_supersedes(self):
        # 纠正：「记错了」→ supersedesId 新版本，旧条目 superseded、版本链完整。
        turn, run = self._send('记住：它上次洗澡是上周六')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        self.assertTrue(self._write_calls(run), '先正确写入一条')
        first = self._memory_rows('episode')[0]
        turn, run = self._send('刚才记错了，它上次洗澡其实是上周日')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        writes = self._write_calls(run)
        self.assertTrue(writes, '纠正应再次写入')
        args = json.loads(writes[-1]['arguments'])
        self.assertTrue(args.get('supersedesId') or args.get('forget'),
                        f'纠正应引用旧条目：{json.dumps(writes, ensure_ascii=False)}')
        rows = self._memory_rows('episode')
        active = [r for r in rows if r.status == 'active']
        self.assertEqual(len(active), 1, '纠正后只有一条当前有效版本')
        self.assertEqual(active[0].version, 2, '版本链应递增')
        self.assertTrue(any(r.status in ('superseded', 'retired') for r in rows), '旧版本保留可追溯')

    def test_E26_write_boundaries_chitchat_vs_dense(self):
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

    def test_E27_empty_store_honest(self):
        # 空库诚实：无记忆时不编造。
        turn, run = self._send('你还记得我上次跟你说的事吗？')
        self.assertEqual(turn['status'], 'complete', turn['reply'])
        calls = [c for c in self._tool_calls(run) if c['name'] == 'search_memories' and c['allowed']]
        self.assertTrue(calls, '应先检索记忆')
        self.assertTrue(any(k in turn['reply'] for k in ('没有', '还没', '不记得', '没记住', '暂时没有')),
                        f'空库应如实说明：{turn["reply"]}')

    def test_E28_forget_on_request(self):
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


if __name__ == '__main__':
    unittest.main()
