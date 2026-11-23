"""W09 周期回顾与硬件事件确定性测试（agent-w09-spec.md §4 A1–A5 的自动验收）。

不依赖真实模型：loop.run_background 的模型往返以假模型（canned JSON / 工具调用脚本）
驱动，覆盖调度边界、硬件规则状态机（走真实 telemetry.ingest_observation 入口）、
主动程度交付、主题去重、学习完成转换与降级路径。真实 MiniMax 行为评测见
agent_behavior.test_agent_behavior 的 E51–E57。
"""
import hashlib
import json
import sys
import unittest
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from helpers import fresh_db

from petsense import db
from petsense.agent import care, care_jobs, jobs
from petsense.agent.routes import bp
from petsense.db import utc_now
from petsense.db.models import AuthSession, Device, Pet, TelemetryMinute, User
from petsense.db.models.agent import (AgentCareTopic, AgentJob, AgentLearningDay, CareDeviceState,
                                       CarePetState, AgentTurn)
from petsense.db.models.memory import PetMemory
from petsense.telemetry.observations import ingest_observation
from sqlalchemy import select

SH = ZoneInfo('Asia/Shanghai')
UTC = timezone.utc


def _at(day_offset=0, hour=9, minute=0, second=0):
    """以上海时间构造测试时钟（相对今天）。"""
    local = datetime.now(SH).replace(hour=hour, minute=minute, second=second, microsecond=0) \
        + timedelta(days=day_offset)
    return local.astimezone(UTC)


def _fake_model(script):
    """假模型：按脚本依次返回（text / function_calls），末项之后重复最后一项。"""
    queue = list(script)

    def respond(_items, tools=None, on_delta=None, max_output_tokens=1800, web_search=False):
        if queue:
            item = queue.pop(0)
        else:
            item = script[-1]
        if isinstance(item, Exception):
            raise item
        return {'text': item.get('text', ''), 'function_calls': item.get('function_calls', []),
                'web_search_calls': [], 'citations': [], 'usage': {}}
    return respond


def _json_out(payload):
    return {'text': json.dumps(payload, ensure_ascii=False)}


class W09CareTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        jobs._HANDLERS.clear()
        care_jobs.register()
        self.addCleanup(jobs._HANDLERS.clear)
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.t0 = utc_now() - timedelta(hours=2)  # 帧时间基准（过去，避免影响回顾窗口）
        with db.session_scope() as s:
            user = User(username='w09', display_name='w09', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user)
            s.flush()
            self.uid = user.id
            pet = Pet(user_id=user.id, name='Leia', species='狗', breed='柴犬',
                      created_at=db.utc_now(), device_id='collar-1')
            s.add(pet)
            s.flush()
            self.pet = pet.id
            # 绑定生效早于测试帧时间（ingest 拒绝 claimed_at 之后的帧）
            s.add(Device(device_id='collar-1', owner_id=user.id, name='',
                         claimed_at=self.t0 - timedelta(hours=1)))
            token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
            self.auth = {'X-Petsense-Session': token}
        with db.session_scope() as s:  # 扫描作业行（默认不可达：仅 reschedule 用例经 tick 驱动）
            jobs.enqueue(s, care_jobs.SCAN_KIND, {}, dedup_key='care:scan:loop',
                         next_run_at=datetime(2100, 1, 1, tzinfo=UTC))

    # ── 帧注入：走真实遥测入口（A2「隔离输入走正常遥测入口」） ──

    def _frame(self, seq, received_at=None, battery=76, health_valid=True, device='collar-1',
               battery_found=True, with_health=True):
        payload = {'deviceId': device, 'seq': seq, 'boot_id': 'b1', 'source': 'esp32_mlp'}
        if battery is not None:
            payload['battery'] = {'found': battery_found, 'percent': battery}
        if with_health:
            payload['health'] = {'valid': health_valid, 'heartRate': 120 if health_valid else 0}
        with db.session_scope() as s:
            return ingest_observation(s, payload, received_at or self.t0 + timedelta(seconds=2 * seq))

    def _topics(self, category=None):
        with db.session_scope() as s:
            stmt = select(AgentCareTopic).where(AgentCareTopic.pet_id == self.pet)
            if category:
                stmt = stmt.where(AgentCareTopic.category == category)
            return s.execute(stmt.order_by(AgentCareTopic.id)).scalars().all()

    def _turns(self, prefix='care:'):
        with db.session_scope() as s:
            rows = s.execute(select(AgentTurn).where(AgentTurn.pet_id == self.pet)
                             .order_by(AgentTurn.id)).scalars().all()
        return [r for r in rows if r.request_id.startswith(prefix)]

    def _set_proactivity(self, level):
        from petsense.db.models.agent import CompanionSettings
        with db.session_scope() as s:
            row = s.get(CompanionSettings, self.uid)
            if row is None:
                row = CompanionSettings(user_id=self.uid, updated_at=utc_now())
                s.add(row)
            row.proactivity = level
            row.updated_at = utc_now()

    def _scan(self, now, connection=None):
        """直接驱动一轮调度扫描（不经 tick，避免顺带执行刚入队的回顾/事件作业）。"""
        job = {'id': 0, 'kind': care_jobs.SCAN_KIND, 'dedup_key': 'care:scan:loop',
               'payload': {}, 'attempts': 0}
        with patch.object(care, 'mqtt_connection_ready',
                          side_effect=(lambda n: connection) if connection is not None
                          else care.mqtt_connection_ready):
            return care_jobs.handle_scan(job, now)

    # ── A1 周期回顾 ─────────────────────────────────────────────────────────

    def test_daily_window_boundary_and_first_enable(self):
        """09:00 前不调度当日回顾；首启水位前的调度点不补跑；到点入队且幂等。"""
        early = _at(0, 8, 59)
        self._scan(early)
        with db.session_scope() as s:
            state = s.get(CarePetState, self.pet)
            self.assertIsNotNone(state)
            reviews = s.execute(select(AgentJob).where(AgentJob.kind == 'care_review')).scalars().all()
        self.assertEqual(reviews, [], '首启当刻之前的窗口不应入队')
        # 水位在 08:59，今天 09:00 的调度点晚于水位 → 应入队回顾昨天
        due = _at(0, 9, 0, 30)
        self._scan(due)
        with db.session_scope() as s:
            reviews = s.execute(select(AgentJob).where(AgentJob.kind == 'care_review')).scalars().all()
        self.assertEqual(len(reviews), 1)
        payload = json.loads(reviews[0].payload_json)
        self.assertEqual(reviews[0].dedup_key,
                         'care:review:daily:%d:%s' % (self.pet, payload['key']))
        expected_day = (datetime.now(SH).replace(hour=9, minute=0, second=0, microsecond=0)
                        - timedelta(days=1)).date().isoformat()
        self.assertEqual(payload['key'], expected_day, '回顾窗口应是上海昨天')

    def test_daily_review_runs_once_and_survives_restart(self):
        """回顾执行一次交付一份；重扫与重启恢复不产生第二份（dedup + request key）。"""
        self._scan(_at(0, 8, 0))  # 首启水位（D4：从下一调度点开始）
        self._scan(_at(0, 9, 30))
        with patch('petsense.agent.model.respond', _fake_model([
            _json_out({'say': '昨天走了 40 分钟，状态不错。', 'level': 'high',
                       'findings': [{'category': 'walk_volume', 'anchor': 'walk-boost-yesterday',
                                     'title': '走动变多', 'text': '昨天走动比前一日多。'}]})])):
            jobs.tick(now=_at(0, 9, 30, 5), limit=5)  # 周一时周回顾同点到点，均应执行
        daily = self._turns('care:review:daily:')
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0].status, 'complete')
        self.assertIn('走了', daily[0].reply)
        # 再次扫描（同日）+ 重启恢复 → 不重复
        self._scan(_at(0, 11, 0))
        jobs.recover_interrupted()
        with patch('petsense.agent.model.respond', _fake_model([
            _json_out({'say': '重复', 'findings': []})])):
            self.assertEqual(jobs.tick(now=_at(0, 11, 0), limit=5), 0)
        self.assertEqual(len(self._turns('care:review:daily:')), 1)

    def test_weekly_window_monday_boundary(self):
        """周一 09:15 回顾上一自然周；09:14 未到点不入队。"""
        # 找一个真实的「本周一」：以当前时间为基准构造周一时刻
        local_now = datetime.now(SH)
        this_monday = local_now - timedelta(days=local_now.weekday())
        monday_914 = this_monday.replace(hour=9, minute=14, second=0, microsecond=0)
        monday_916 = this_monday.replace(hour=9, minute=16, second=0, microsecond=0)
        # 首启水位放在上周六（早于本周一调度点）
        self._scan(monday_914.astimezone(UTC) - timedelta(days=2))
        self._scan(monday_914.astimezone(UTC))
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(AgentJob).where(
                AgentJob.kind == 'care_review',
                AgentJob.payload_json.like('%"weekly"%'))).scalars().all(), [],
                '周回顾 09:15 未到点不入队')
        self._scan(monday_916.astimezone(UTC))
        with db.session_scope() as s:
            reviews = s.execute(select(AgentJob).where(
                AgentJob.kind == 'care_review',
                AgentJob.payload_json.like('%"weekly"%'))).scalars().all()
        self.assertEqual(len(reviews), 1)
        payload = json.loads(reviews[0].payload_json)
        expected_week = (this_monday - timedelta(days=7)).date().isoformat()
        self.assertEqual(payload['key'], expected_week, '周窗口=上一自然周的周一')

    def test_interruption_replays_latest_windows_only(self):
        """启用后中断多日：恢复时只补最新日窗口与周窗口各一个（D4）。"""
        enable = _at(-7, 8, 0)  # 一周前 08:00 启用
        self._scan(enable)
        # 模拟中断：直接在 3 天后 10:00 恢复扫描
        resume = _at(-3, 10, 0)
        self._scan(resume)
        with db.session_scope() as s:
            reviews = s.execute(select(AgentJob).where(AgentJob.kind == 'care_review')).scalars().all()
        kinds = {}
        for row in reviews:
            kind = json.loads(row.payload_json)['kind']
            kinds[kind] = kinds.get(kind, 0) + 1
        self.assertEqual(kinds.get('daily', 0), 1, '中断后只补最新一个日窗口')
        self.assertLessEqual(kinds.get('weekly', 0), 1, '周窗口至多一个')
        daily = next(json.loads(r.payload_json) for r in reviews
                     if json.loads(r.payload_json)['kind'] == 'daily')
        expected_day = (datetime.now(SH) - timedelta(days=4)).date().isoformat()  # resume 的昨天
        self.assertEqual(daily['key'], expected_day)

    def test_review_without_data_zero_findings(self):
        """无数据回顾：可以零发现，不发消息，轨迹如实保存。"""
        self._scan(_at(0, 8, 0))
        self._scan(_at(0, 9, 30))
        with patch('petsense.agent.model.respond', _fake_model([
            _json_out({'say': '', 'findings': []})])):
            jobs.tick(now=_at(0, 9, 30, 5), limit=5)
        self.assertEqual(self._turns(), [])
        with db.session_scope() as s:
            from petsense.db.models.agent import AgentRun
            run = s.execute(select(AgentRun).where(AgentRun.pet_id == self.pet)
                            .order_by(AgentRun.id.desc())).scalars().first()
        self.assertEqual(run.form, 'review')
        conclusion = json.loads(run.conclusion)
        self.assertEqual(conclusion['findings'], 0)

    def _seed_yesterday_minutes(self, minutes=60, device='collar-1'):
        """种上海「昨天」的观测分钟行（回顾窗口=昨天 00:00→今天 00:00）。"""
        today = datetime.now(SH).replace(hour=0, minute=0, second=0, microsecond=0)
        start = (today - timedelta(days=1)).astimezone(UTC)
        with db.session_scope() as s:
            for i in range(minutes):
                s.add(TelemetryMinute(owner_id=self.uid, device_id=device,
                                      minute=start + timedelta(minutes=i), metrics='{}',
                                      actions=json.dumps({'走动': 30}),
                                      observed_seconds=60, frames=60))

    def test_review_degrades_to_facts_without_model(self):
        """模型不可用：回顾按事实降级交付（标准档），轨迹记 degraded。"""
        self._seed_yesterday_minutes()
        self._scan(_at(0, 8, 0))
        self._scan(_at(0, 9, 30))
        with patch('petsense.agent.model.respond',
                   _fake_model([RuntimeError('no credentials')])):
            jobs.tick(now=_at(0, 9, 30, 5), limit=5)
        daily = self._turns('care:review:daily:')
        self.assertEqual(len(daily), 1)
        # 有数据时降级文案为「观察小结」逐日事实（只报统计，不推断）
        self.assertIn('观察小结', daily[0].reply)
        with db.session_scope() as s:
            from petsense.db.models.agent import AgentRun
            run = s.execute(select(AgentRun).where(AgentRun.pet_id == self.pet)
                            .order_by(AgentRun.id.desc())).scalars().first()
        self.assertEqual(run.status, 'degraded')
        self.assertIn('model_unavailable', json.loads(run.conclusion)['reason_code'])

    def test_review_degraded_without_data_has_no_message(self):
        """无数据的降级回顾：只留轨迹不发消息（无数据不打扰，spec §2 D2）。"""
        self._scan(_at(0, 8, 0))
        self._scan(_at(0, 9, 30))
        with patch('petsense.agent.model.respond',
                   _fake_model([RuntimeError('no credentials')])):
            jobs.tick(now=_at(0, 9, 30, 5), limit=5)
        self.assertEqual(self._turns('care:review:daily:'), [], '无数据不发主动消息')
        with db.session_scope() as s:
            from petsense.db.models.agent import AgentRun
            run = s.execute(select(AgentRun).where(AgentRun.pet_id == self.pet)
                            .order_by(AgentRun.id.desc())).scalars().first()
        self.assertEqual(run.status, 'degraded')
        self.assertEqual(json.loads(run.conclusion)['delivered'], False)

    # ── A2 硬件事件 ─────────────────────────────────────────────────────────

    def test_battery_open_dedup_escalate_recover_recur(self):
        """低电：3 个不同合格帧开启；重复帧不推进；<10% 升级一次；回稳恢复；再发生新实例。"""
        for seq in (1, 2, 3):
            self.assertTrue(self._frame(seq, battery=15))
        topics = self._topics('battery_low')
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].status, 'open')
        self.assertEqual(topics[0].severity, 'warning')
        # 重复帧（同序号被 ingest 拒绝）不推进、不新开
        self.assertFalse(self._frame(3, battery=15))
        self.assertEqual(len(self._topics('battery_low')), 1)
        # 升级：<10% 触发 escalate 阶段（同一实例）
        self.assertTrue(self._frame(4, battery=8))
        topics = self._topics('battery_low')
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].severity, 'critical')
        with db.session_scope() as s:
            escalate_jobs = s.execute(select(AgentJob).where(
                AgentJob.kind == 'care_event',
                AgentJob.payload_json.like('%"escalate"%'))).scalars().all()
        self.assertEqual(len(escalate_jobs), 1)
        # 回稳：连续 3 帧 >25% 恢复（不发消息，主题翻 recovered）
        for seq in (5, 6, 7):
            self._frame(seq, battery=26)
        topics = self._topics('battery_low')
        self.assertEqual(topics[0].status, 'recovered')
        self.assertIsNotNone(topics[0].recovered_at)
        # 再发生：3 帧低电 → 新实例（instance=2）
        for seq in (8, 9, 10):
            self._frame(seq, battery=12)
        topics = self._topics('battery_low')
        self.assertEqual(len(topics), 2)
        self.assertEqual(topics[1].instance, 2)
        self.assertEqual(topics[1].status, 'open')

    def test_battery_events_deliver_messages_per_phase(self):
        for seq in (1, 2, 3):
            self._frame(seq, battery=15)
        self._frame(4, battery=8)
        with patch('petsense.agent.model.respond', _fake_model([
            _json_out({'say': '项圈电量偏低，方便时充下电。', 'disturb': True}),
            _json_out({'say': '电量已经很低了，建议尽快充电。', 'disturb': True}),
        ])):
            jobs.tick(now=self.t0 + timedelta(minutes=10), limit=5)
        turns = self._turns('care:event')
        self.assertEqual(len(turns), 2, '首发 + 升级各一条，恢复不发')
        topics = self._topics('battery_low')
        self.assertEqual(topics[0].delivered, 2)
        self.assertEqual(topics[0].run_id, topics[0].run_id)  # run 关联存在
        blocks = json.loads(turns[0].blocks)
        self.assertEqual(blocks[0]['type'], 'text')
        self.assertEqual(blocks[1]['type'], 'care')
        self.assertEqual(blocks[1]['care']['topicId'], topics[0].id)

    def test_invalid_readings_require_sustained_frames(self):
        """长期无效：90 帧无效开启（缺字段不计）、新有效帧恢复。"""
        for seq in range(1, 90):
            self._frame(seq, health_valid=False)
        self.assertEqual(self._topics('readings_invalid'), [], '89 帧不足')
        self._frame(90, health_valid=False, with_health=False)  # 缺字段不计作无效
        self.assertEqual(self._topics('readings_invalid'), [], '缺字段帧不推进')
        self._frame(91, health_valid=False)
        topics = self._topics('readings_invalid')
        self.assertEqual(len(topics), 1)
        self._frame(92, health_valid=True)
        self.assertEqual(self._topics('readings_invalid')[0].status, 'recovered')

    def test_stale_requires_connection_and_recovers_on_frame(self):
        """失联：连接不可用不判定；连接稳定且 >30 秒无帧开启；新帧恢复。"""
        self._frame(1)
        with db.session_scope() as s:
            last = s.get(CareDeviceState, 'collar-1').last_frame_at
        later = last + timedelta(seconds=45)
        self._scan(later, connection=False)
        self.assertEqual(self._topics('data_stale'), [], 'MQTT 故障不得编造失联')
        self._scan(later, connection=True)
        topics = self._topics('data_stale')
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].severity, 'info')
        # 新帧到达即恢复
        self._frame(2, received_at=later + timedelta(seconds=5))
        self.assertEqual(self._topics('data_stale')[0].status, 'recovered')

    def test_stale_never_online_device_not_fabricated(self):
        """从未在线（无水位）的设备不判定失联。"""
        self._scan(_at(0, 12, 0), connection=True)
        self.assertEqual(self._topics('data_stale'), [])

    def test_unbound_and_rebind_do_not_fabricate(self):
        """未绑定设备的帧不产生宠物事件；换绑把旧主题可审计关闭。"""
        # 另一用户认领了 collar-2（无宠物档案：设备已绑定但无归属宠物）
        with db.session_scope() as s:
            other = User(username='w09n', display_name='n', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=utc_now())
            s.add(other)
            s.flush()
            s.add(Device(device_id='collar-2', owner_id=other.id, name='',
                         claimed_at=self.t0 - timedelta(hours=1)))
        for seq in (1, 2, 3):
            self._frame(seq, battery=15, device='collar-2')
        self.assertEqual(self._topics('battery_low'), [], '无宠物归属不产生事件')
        # 换绑：collar-1 转给新用户（pets.device_id 唯一，原宠物先解绑）
        with db.session_scope() as s:
            user2 = User(username='w09b', display_name='b', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user2)
            s.flush()
            s.get(Pet, self.pet).device_id = ''
            s.add(Pet(user_id=user2.id, name='Momo', species='猫', breed='', created_at=utc_now(),
                      device_id='collar-1'))
            device = s.get(Device, 'collar-1')
            device.owner_id = user2.id
            device.claimed_at = utc_now()
        later = utc_now()
        for seq in (1, 2, 3):
            with db.session_scope() as s:
                ingest_observation(s, {'deviceId': 'collar-1', 'seq': seq, 'boot_id': 'b2',
                                       'battery': {'found': True, 'percent': 15},
                                       'health': {'valid': True}}, later + timedelta(seconds=2 * seq))
        topics = self._topics('battery_low')
        self.assertEqual(topics, [], '原宠物名下无事件')

    def test_backfill_replay_rebuilds_watermark_without_historical_events(self):
        """启动重放历史帧：水位重建，但历史转换不开题、不入队、不发消息（首启语义）。"""
        care.set_replay_mode(True)
        try:
            for seq in (1, 2, 3):
                self._frame(seq, battery=15)
        finally:
            care.set_replay_mode(False)
        self.assertEqual(self._topics(), [], '历史低电不为首启补发事件')
        with db.session_scope() as s:
            state = s.get(CareDeviceState, 'collar-1')
            self.assertGreaterEqual(state.battery_violation, 3, '水位仍按历史帧重建')
            self.assertEqual(s.execute(select(AgentJob).where(
                AgentJob.kind == 'care_event')).scalars().all(), [])
        # 重放后真实新帧（仍低电）按当前时间开题——不是历史补发
        self._frame(4, battery=14, received_at=utc_now())
        topics = self._topics('battery_low')
        self.assertEqual(len(topics), 1)
        self.assertGreater(topics[0].opened_at, self.t0 + timedelta(minutes=1))

    def test_rebind_closes_open_topics_auditably(self):
        """已开启主题在换绑时按 closed_reason=rebind 关闭（可审计，不算恢复）。"""
        for seq in (1, 2, 3):
            self._frame(seq, battery=15)
        self.assertEqual(self._topics('battery_low')[0].status, 'open')
        with db.session_scope() as s:
            user2 = User(username='w09c', display_name='c', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user2)
            s.flush()
            device = s.get(Device, 'collar-1')
            device.owner_id = user2.id
            device.claimed_at = utc_now()
        # 新归属的帧：触发换绑关闭（帧时间须晚于新 claimed_at，boot 换新避免序号门）
        with db.session_scope() as s:
            ingest_observation(s, {'deviceId': 'collar-1', 'seq': 1, 'boot_id': 'b9',
                                   'battery': {'found': True, 'percent': 15},
                                   'health': {'valid': True}}, utc_now() + timedelta(seconds=5))
        topic = self._topics('battery_low')[0]
        self.assertEqual(topic.status, 'closed')
        self.assertEqual(topic.closed_reason, 'rebind')

    # ── A3 主动程度与主题 ───────────────────────────────────────────────────

    def test_quiet_level_persists_topic_without_message(self):
        """安静档：主题可追溯但不发主动会话消息。"""
        self._set_proactivity(0)
        for seq in (1, 2, 3):
            self._frame(seq, battery=15)
        with patch('petsense.agent.model.respond', _fake_model([
            _json_out({'say': '项圈电量偏低。', 'disturb': True})])):
            jobs.tick(now=self.t0 + timedelta(minutes=10), limit=5)
        self.assertEqual(len(self._topics('battery_low')), 1)
        self.assertEqual(self._turns(), [], '安静档不发主动会话消息')
        response = self.client.get(f'/api/app/agent/pets/{self.pet}/care/topics',
                                   headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['topics']), 1, '主题仍可在伙伴页追溯')

    def test_standard_respects_model_no_disturb(self):
        """标准档：模型明确不值得打扰时不交付（事实仍入主题与轨迹）。"""
        for seq in (1, 2, 3):
            self._frame(seq, battery=19)
        with patch('petsense.agent.model.respond', _fake_model([
            _json_out({'say': '电量轻微偏低，先不打扰。', 'disturb': False})])):
            jobs.tick(now=self.t0 + timedelta(minutes=10), limit=5)
        self.assertEqual(self._turns(), [])
        self.assertEqual(len(self._topics('battery_low')), 1)

    def test_review_finding_anchor_dedup_between_windows(self):
        """日/周回顾同锚点不重复提报：第二次只更新既有主题，不建新主题。"""
        self._scan(_at(0, 8, 0))
        self._scan(_at(0, 9, 30))
        finding = {'category': 'walk_volume', 'anchor': 'walk-boost-this-week',
                   'title': '走动变多', 'text': '这几天走动比前几日多。'}
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '昨天走了 60 分钟。', 'findings': [finding]})])):
            jobs.tick(now=_at(0, 9, 30, 5), limit=5)
        observation_topics = self._topics()
        self.assertEqual(len(observation_topics), 1)
        self.assertEqual(observation_topics[0].anchor, 'walk-boost-this-week')
        # 周回顾再次提报同锚点 → 合并进既有主题，不新建
        with db.session_scope() as s:
            week = _at(-3, 10, 0)
            state = s.get(CarePetState, self.pet)
            state.enabled_at = week - timedelta(days=7)
            jobs.enqueue(s, 'care_review',
                         {'pet': self.pet, 'kind': 'weekly', 'key': '2026-11-16',
                          'windowStart': '2026-11-16T00:00:00+08:00',
                          'windowEnd': '2026-11-23T00:00:00+08:00',
                          'scheduledAt': '2026-11-23T09:15:00+08:00'},
                         dedup_key='care:review:weekly:%d:2026-11-16' % self.pet,
                         next_run_at=week)
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '这周整体走动不错。', 'findings': [dict(finding, text='周汇总里继续关注。')]})])):
            jobs.tick(now=week + timedelta(minutes=1), limit=5)
        self.assertEqual(len([t for t in self._topics() if t.kind == 'observation']), 1,
                         '同锚点不得重复开题')

    def test_event_form_rejects_write_tools(self):
        """事件形态只允许事实读取：write_memory 被权限矩阵拒绝且不落库（E56 确定性版）。"""
        for seq in (1, 2, 3):
            self._frame(seq, battery=15)
        with patch('petsense.agent.model.respond', _fake_model([
            {'text': '', 'function_calls': [{'name': 'write_memory', 'call_id': 'c1',
                                             'arguments': json.dumps({'kind': 'episode',
                                                                      'summary': 'x'})}]},
            _json_out({'say': '项圈电量偏低。', 'disturb': True}),
        ])):
            jobs.tick(now=self.t0 + timedelta(minutes=10), limit=5)
        with db.session_scope() as s:
            self.assertEqual(s.execute(select(PetMemory).where(
                PetMemory.pet_id == self.pet)).scalars().all(), [])
            from petsense.db.models.agent import AgentRun
            run = s.execute(select(AgentRun).where(AgentRun.pet_id == self.pet)
                            .order_by(AgentRun.id.desc())).scalars().first()
        calls = json.loads(run.tool_summary)
        self.assertTrue(calls and calls[0]['name'] == 'write_memory'
                        and calls[0]['reason'] == 'form_not_allowed')
        self.assertEqual(len(self._turns('care:event')), 1, '拒绝后正常运行并交付')

    # ── A5 学习完成 ─────────────────────────────────────────────────────────

    def _seed_learning(self, days, feature='actions', version='v3'):
        """重建该 (维度, 版本) 的有效日（每天 ≥6h、跨 ≥2 时段）。"""
        with db.session_scope() as s:
            for row in s.execute(select(AgentLearningDay).where(
                    AgentLearningDay.pet_id == self.pet,
                    AgentLearningDay.feature == feature,
                    AgentLearningDay.version == version)).scalars().all():
                s.delete(row)
            for offset in range(days):
                day = (datetime.now(SH) - timedelta(days=offset + 1)).strftime('%Y-%m-%d')
                s.add(AgentLearningDay(pet_id=self.pet, device_id='collar-1',
                                       feature=feature, version=version, day=day,
                                       seconds=30000, last_seen_at=utc_now(),
                                       slots=json.dumps([0, 2])))

    def test_learning_ready_fires_once_and_initializes_watermark(self):
        """首扫只初始化水位（6 天无事件）；真实转换发一次；重复扫描不重发。"""
        self._seed_learning(6)
        self._scan(_at(0, 12, 0))
        self.assertEqual(self._topics('learning_ready'), [], '首启不把已有进度当新事件')
        self._seed_learning(7)  # 凑满第 7 个有效日 → ready
        self._scan(_at(0, 12, 1))
        topics = self._topics('learning_ready')
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].anchor, 'actions:v3')
        self._scan(_at(0, 12, 2))
        self.assertEqual(len(self._topics('learning_ready')), 1, '同键终身一次')
        with patch('petsense.agent.model.respond', _fake_model([
            _json_out({'say': '连续观察凑够 7 天，开始认识它的日常了。', 'disturb': True})])):
            jobs.tick(now=_at(0, 12, 3), limit=5)
        self.assertEqual(len(self._turns('care:event')), 1)

    def test_learning_new_version_can_relearn(self):
        """版本切换后重新累计，就绪可再次报告（新键）。"""
        self._seed_learning(6)
        self._scan(_at(0, 12, 0))  # 首扫初始化水位
        self._seed_learning(7)
        self._scan(_at(0, 12, 1))
        self.assertEqual(len(self._topics('learning_ready')), 1)
        self._seed_learning(1, version='v4')
        self._scan(_at(0, 12, 2))
        self.assertEqual(len(self._topics('learning_ready')), 1, 'v4 认识中不报告')
        self._seed_learning(7, version='v4')
        self._scan(_at(0, 12, 3))
        topics = self._topics('learning_ready')
        self.assertEqual(len(topics), 2)
        self.assertEqual(topics[1].anchor, 'actions:v4')

    # ── 能力端点与鉴权 ──────────────────────────────────────────────────────

    def test_care_rules_capability_endpoint(self):
        response = self.client.get(f'/api/app/agent/pets/{self.pet}/care/rules', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['managedRules'],
                         ['battery_low', 'data_stale', 'readings_invalid'])
        self.assertEqual(response.json['capability'], 'care-v1')
        # 未鉴权与其他用户均不可读
        self.assertEqual(self.client.get(
            f'/api/app/agent/pets/{self.pet}/care/rules').status_code, 401)
        with db.session_scope() as s:
            other = User(username='w09x', display_name='x', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=utc_now())
            s.add(other)
            s.flush()
            token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=other.id, expires_at=utc_now() + timedelta(days=1)))
        self.assertEqual(self.client.get(
            f'/api/app/agent/pets/{self.pet}/care/topics',
            headers={'X-Petsense-Session': token}).status_code, 404)

    def test_read_care_topics_tool_answers_why(self):
        """对话工具 read_care_topics：查主题与依据供「为什么提醒我」追问。"""
        for seq in (1, 2, 3):
            self._frame(seq, battery=15)
        from petsense.agent import registry
        gate = registry.execute_tool('read_care_topics', {}, registry.DIALOGUE, 'allowed',
                                     {'pet_id': self.pet, 'user_id': self.uid})
        self.assertTrue(gate['allowed'])
        self.assertEqual(len(gate['result']['topics']), 1)
        self.assertEqual(gate['result']['topics'][0]['category'], 'battery_low')
        self.assertTrue(gate['result']['topics'][0]['evidence'], '依据帧随主题交付')

    def test_background_form_schema_excludes_writes(self):
        """review/event 形态 schema 只含事实读取工具（不含基线/记忆/写入/外读）。"""
        from petsense.agent import registry
        for form in (registry.REVIEW, registry.EVENT):
            names = {t['name'] for t in registry.schemas_for(form, 'allowed', search_enabled=False)}
            self.assertEqual(names, {'query_observations', 'read_current_status', 'read_pet_profile'},
                             '%s 形态工具面' % form)

    def test_scan_reschedules_itself(self):
        now = _at(0, 12, 0)
        with db.session_scope() as s:
            row = s.execute(select(AgentJob).where(AgentJob.dedup_key == 'care:scan:loop')).scalar_one()
            row.next_run_at = now - timedelta(seconds=1)
        self.assertEqual(jobs.tick(now=now, limit=3), 1)
        with db.session_scope() as s:
            row = s.execute(select(AgentJob).where(AgentJob.dedup_key == 'care:scan:loop')).scalar_one()
        self.assertEqual(row.status, 'pending')
        self.assertGreater(row.next_run_at, now)


class W09ProactivitySwitchTests(W09CareTests):
    """切档后实际生效（W09 §4 A4）：安静档开题不发消息，改档后补发一次首发消息。"""

    def test_proactivity_switch_delivers_pending_open_topic_once(self):
        self._set_proactivity(0)
        for seq in (1, 2, 3):
            self._frame(seq, battery=15)
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '项圈电量偏低。', 'disturb': True})])):
            jobs.tick(now=self.t0 + timedelta(minutes=10), limit=5)
        self.assertEqual(len(self._topics('battery_low')), 1, '主题已开')
        self.assertEqual(self._turns('care:event:'), [], '安静档不发主动会话消息')

        # 改成标准档：扫描按新档位补排事件作业，首发消息补发一次
        self._set_proactivity(1)
        self._scan(self.t0 + timedelta(minutes=20))
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '项圈电量偏低，方便时帮它充下电。', 'disturb': True})])):
            jobs.tick(now=self.t0 + timedelta(minutes=21), limit=5)
        self.assertEqual(len(self._turns('care:event:')), 1, '切档后补发一条首发消息')

        # 同档位再扫不重复发
        self._scan(self.t0 + timedelta(minutes=30))
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '又来一次。', 'disturb': True})])):
            jobs.tick(now=self.t0 + timedelta(minutes=31), limit=5)
        self.assertEqual(len(self._turns('care:event:')), 1, '同一事件实例只发一条首发消息')
class W09ReviewIntegrationTests(W09CareTests):
    """W09 评审修复的确定性回归：消息去重、主动程度门槛、调度资格、WS 交付与轨迹。"""

    def _review_job(self, kind='daily', key='2026-11-19', start='2026-11-19T00:00:00+08:00',
                    end='2026-11-20T00:00:00+08:00', scheduled='2026-11-20T09:00:00+08:00',
                    now=None):
        with db.session_scope() as s:
            jobs.enqueue(s, 'care_review',
                         {'pet': self.pet, 'kind': kind, 'key': key,
                          'windowStart': start, 'windowEnd': end, 'scheduledAt': scheduled},
                         dedup_key='care:review:%s:%d:%s' % (kind, self.pet, key),
                         next_run_at=now or self.t0)

    def test_say_without_finding_has_no_message(self):
        """模型只说话不给发现：没有授权证据 → 只留轨迹不发主动消息。"""
        self._review_job()
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '昨天走了 40 分钟。', 'findings': []})])):
            jobs.tick(now=self.t0 + timedelta(minutes=1), limit=5)
        self.assertEqual(self._turns('care:review:daily:'), [], '无发现支撑的消息不发布')
        with db.session_scope() as s:
            from petsense.db.models.agent import AgentRun
            run = s.execute(select(AgentRun).where(AgentRun.pet_id == self.pet)
                            .order_by(AgentRun.id.desc())).scalars().first()
        conclusion = json.loads(run.conclusion)
        self.assertEqual(conclusion['authorized'], False)
        self.assertEqual(conclusion['delivered'], False)

    def test_standard_level_gates_normal_finding(self):
        """标准档只发高相关发现；同一条一般发现（level=normal）在多提醒档才发。"""
        finding = {'category': 'walk_volume', 'anchor': 'walk-normal', 'title': '走动',
                   'text': '走动和平时差不多。'}
        # 标准档 + level=normal → 不发
        self._set_proactivity(1)
        self._review_job()
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '今天它和平时差不多。', 'level': 'normal', 'findings': [finding]})])):
            jobs.tick(now=self.t0 + timedelta(minutes=1), limit=5)
        self.assertEqual(self._turns('care:review:daily:'), [], '标准档不发一般发现')
        # 多提醒档 + 同一发现 → 发
        self._set_proactivity(2)
        with db.session_scope() as s:
            row = s.execute(select(AgentCareTopic).where(
                AgentCareTopic.pet_id == self.pet, AgentCareTopic.kind == 'observation')).scalar_one()
            row.status = 'recovered'   # 允许新实例再开（避免同实例重复）
            row.recovered_at = utc_now()
        self._review_job(key='2026-11-20', start='2026-11-20T00:00:00+08:00',
                         end='2026-11-21T00:00:00+08:00')
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '今天它和平时差不多。', 'level': 'normal', 'findings': [finding]})])):
            jobs.tick(now=self.t0 + timedelta(minutes=2), limit=5)
        self.assertEqual(len(self._turns('care:review:daily:')), 1, '多提醒档可发一般发现')

    def test_same_anchor_not_reported_twice(self):
        """日/周回顾同锚点：第二次只更新主题，不再发第二条消息。"""
        finding = {'category': 'walk_volume', 'anchor': 'walk-boost-week', 'title': '走动变多',
                   'text': '这几天走动比前几日多。'}
        self._review_job()
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '昨天走动变多。', 'level': 'high', 'findings': [finding]})])):
            jobs.tick(now=self.t0 + timedelta(minutes=1), limit=5)
        self.assertEqual(len(self._turns('care:review:daily:')), 1)
        self._review_job(kind='weekly', key='2026-11-16',
                         start='2026-11-16T00:00:00+08:00', end='2026-11-23T00:00:00+08:00',
                         scheduled='2026-11-23T09:15:00+08:00', now=self.t0 + timedelta(minutes=2))
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '这周走动也变多。', 'level': 'high',
                           'findings': [dict(finding, text='周汇总里仍然提到同一件事。')]})])):
            jobs.tick(now=self.t0 + timedelta(minutes=3), limit=5)
        self.assertEqual(len(self._turns('care:review:')), 1, '同一锚点不得重复发消息')
        with db.session_scope() as s:
            topics = s.execute(select(AgentCareTopic).where(
                AgentCareTopic.pet_id == self.pet, AgentCareTopic.kind == 'observation')).scalars().all()
        self.assertEqual(len(topics), 1, '同锚点不重复开题')
        self.assertGreaterEqual(len(json.loads(topics[0].evidence)), 2, '既有主题的依据被更新')

    def test_eligibility_uses_owned_observations_or_current_binding(self):
        """调度资格：真实可用观测或当前绑定；设备已被别人认领则不合格。"""
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            self.assertTrue(care.review_eligible(s, pet))
            # 无绑定但有严格归属的真实观测 → 合格
            pet.device_id = 'collar-unbound'
            s.add(TelemetryMinute(owner_id=self.uid, device_id='collar-unbound',
                                  minute=utc_now() - timedelta(hours=1), metrics='{}',
                                  actions=json.dumps({'走动': 10}), observed_seconds=60, frames=60))
            s.flush()
            self.assertTrue(care.review_eligible(s, pet))
            # 设备被别人认领 → 不合格（不把旧设备算给任何人）
            other = User(username='w09-other', display_name='other', password_hash='x',
                         salt='s', avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(other)
            s.flush()
            s.add(Device(device_id='collar-unbound', owner_id=other.id, name='',
                         claimed_at=self.t0 - timedelta(hours=1)))
            self.assertFalse(care.review_eligible(s, pet))

    def test_review_run_trace_keeps_actual_model_trace(self):
        """轨迹保留真实模型回合、工具与预算，并记录窗口与主动程度（可审计）。"""
        self._review_job()
        with patch('petsense.agent.model.respond', _fake_model([
                {'text': '', 'function_calls': [{'name': 'query_observations', 'call_id': 'c1',
                                                 'arguments': json.dumps({'start': '2026-11-19T00:00:00+08:00',
                                                                         'end': '2026-11-20T00:00:00+08:00'})}]},
                _json_out({'say': '昨天它走动偏多。', 'level': 'high',
                           'findings': [{'category': 'walk_volume', 'anchor': 'walk-x',
                                         'title': '走动', 'text': '走动偏多。'}]})])):
            jobs.tick(now=self.t0 + timedelta(minutes=1), limit=5)
        with db.session_scope() as s:
            from petsense.db.models.agent import AgentRun
            run = s.execute(select(AgentRun).where(AgentRun.pet_id == self.pet)
                            .order_by(AgentRun.id.desc())).scalars().first()
            summary = {
                'conclusion': json.loads(run.conclusion),
                'messages': json.loads(run.messages),
                'tool_summary': json.loads(run.tool_summary),
                'budget': json.loads(run.budget),
                'snapshot': json.loads(run.snapshot) if run.snapshot else None,
            }
        self.assertEqual(summary['conclusion']['window'], '2026-11-19')
        self.assertEqual(summary['conclusion']['proactivity'], 1)
        self.assertEqual(summary['conclusion']['trigger']['windowStart'],
                         '2026-11-19T00:00:00+08:00')
        self.assertTrue(summary['tool_summary'], '工具调用须留在轨迹')
        self.assertEqual(summary['tool_summary'][0]['name'], 'query_observations')
        self.assertGreaterEqual(summary['budget']['rounds'], 1)
        self.assertIn('reviewFacts', summary['snapshot'])
        self.assertEqual(summary['snapshot']['settings']['proactivity'], 1)

    def test_background_delivery_pushes_single_wellformed_frame(self):
        """后台交付：WS 只推一帧，且 turn.blocks 已解析为数组（只序列化一次）。"""
        from petsense.agent import ws as agent_ws

        class FakeSocket:
            def __init__(self):
                self.frames = []

            def send(self, payload):
                self.frames.append(payload)

        socket = FakeSocket()
        agent_ws.clients[socket] = self.uid
        self.addCleanup(agent_ws.clients.pop, socket, None)
        self._review_job()
        with patch('petsense.agent.model.respond', _fake_model([
                _json_out({'say': '昨天它走动偏多。', 'level': 'high',
                           'findings': [{'category': 'walk_volume', 'anchor': 'walk-ws',
                                         'title': '走动', 'text': '走动偏多。'}]})])):
            jobs.tick(now=self.t0 + timedelta(minutes=1), limit=5)
        self.assertEqual(len(socket.frames), 1, '只推一帧')
        frame = json.loads(socket.frames[0])
        self.assertEqual(frame['type'], 'turn')
        blocks = frame['turn']['blocks']
        self.assertIsInstance(blocks, list, 'blocks 应为已解析的数组（不得二次序列化）')
        self.assertTrue(any(block.get('type') == 'care' for block in blocks))
        self.assertTrue(any(block.get('type') == 'text' for block in blocks))


if __name__ == '__main__':
    unittest.main()
