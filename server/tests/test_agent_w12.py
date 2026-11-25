"""W12 收件箱与通知定位的确定性测试（agent-w12-spec.md §验收标准 A1–A4 的自动部分）。

不依赖真实模型与外部服务：来源用真实 ORM 行构造（W09 主题 / W08 交付），
收件箱条目由可恢复投影产生。覆盖：
- 四类来源各产生一条、重启/重投/重复投影不重复（W12-A1）
- 精确的来源→回合身份（事件 open/escalate、回顾日/周口径）（复核意见 1）
- 水位早于发布时由富化补齐身份与预览，不复活已读（W12-A1，复核意见 2）
- 启用只补当下仍开放的设备告警、其余类型从启用后入箱（W12-A1/A2）
- 预览优先级、未读计数、学习完成不预览、设备关闭后退出关键优先级（W12-A2）
- 中心策略：推送候选（设备/回信/个人高相关），缺失相关性不得按 high（W13 D7）
- 已读语义：幂等、不解除告警、全部已读按可见水位、宠物过滤拒绝他人（W12-A3）
- 深链解析：锚点、纯读解析、来源不可访问、跨账号隔离（W12-A3/A4）
- 打开历史条目不产生新回合（W12-A4）
"""
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from helpers import fresh_db, new_conversation
from sqlalchemy import select

from petsense import db
from petsense.agent import inbox, inbox_jobs
from petsense.agent.inbox_routes import bp
from petsense.db import utc_now
from petsense.db.models import AuthSession, Pet, User
from petsense.db.models.agent import (
    AgentCareTopic,
    AgentConversation,
    AgentInboxItem,
    AgentInboxState,
    AgentTask,
    AgentTaskDelivery,
    AgentTaskPlan,
    AgentTurn,
)

HEADER = 'X-Petsense-Session'


def _sha(value):
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()


class W12InboxTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.now = utc_now()
        self._auto_enable = True   # 夹具默认在「启用」之后创建来源（对齐部署语义）
        self._conversations = {}
        with db.session_scope() as s:
            user = User(username='w12', display_name='w12', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(user)
            s.flush()
            self.uid = user.id
            self.token = 'token-w12'
            s.add(AuthSession(token_hash=_sha(self.token), user_id=user.id,
                              expires_at=self.now + timedelta(days=7)))
            pet = Pet(user_id=user.id, name='豆豆', species='dog', device_id='dev-w12',
                      created_at=self.now)
            s.add(pet)
            s.flush()
            self.pet_id = pet.id
            pet2 = Pet(user_id=user.id, name='团团', species='cat', device_id='dev-w12-2',
                       created_at=self.now)
            s.add(pet2)
            s.flush()
            self.pet2_id = pet2.id
            other = User(username='w12-other', display_name='other', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(other)
            s.flush()
            self.other_uid = other.id
            self.other_token = 'token-other'
            s.add(AuthSession(token_hash=_sha(self.other_token), user_id=other.id,
                              expires_at=self.now + timedelta(days=7)))
            other_pet = Pet(user_id=other.id, name='别的狗', species='dog', device_id='dev-other',
                            created_at=self.now)
            s.add(other_pet)
            s.flush()
            self.other_pet_id = other_pet.id

    # ── 夹具 ──────────────────────────────────────────────────────────────
    def _ensure_enabled(self):
        """首次投影 = 启用（对齐生产：部署时确立启用水位）。"""
        if not self._auto_enable:
            return
        self._auto_enable = False
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)

    def _topic(self, kind, category, anchor, *, status='open', severity='warning',
               opened_at=None, evidence=None, detail='', title=None, pet_id=None,
               closed_reason=None):
        self._ensure_enabled()
        with db.session_scope() as s:
            moment = opened_at or self.now
            row = AgentCareTopic(pet_id=pet_id or self.pet_id, kind=kind, category=category,
                                 anchor=anchor, instance=1, status=status, severity=severity,
                                 title=title or category, detail=detail,
                                 evidence=json.dumps(evidence or [], ensure_ascii=False),
                                 delivered=0, opened_at=moment, updated_at=moment,
                                 closed_reason=closed_reason)
            s.add(row)
            s.flush()
            return row.id

    def _conversation_for(self, pet_id):
        """这只宠物的会话（W16 起回合必须属于某个会话，否则深链取不回）。"""
        if pet_id not in self._conversations:
            self._conversations[pet_id] = new_conversation(pet_id)
        return self._conversations[pet_id]

    def _turn(self, request_id, *, topic_id=None, text='好的', blocks=None, pet_id=None):
        # 会话要在进入事务之前取：new_conversation 自己开会话，而 session_scope 不可嵌套
        pet_id, conversation_id = pet_id or self.pet_id, self._conversation_for(pet_id or self.pet_id)
        with db.session_scope() as s:
            row = AgentTurn(pet_id=pet_id, conversation_id=conversation_id,
                            request_id=request_id, text='',
                            target='assistant', topic_id=topic_id, reply_to=None, sources='[]',
                            speaker='assistant', reply=text, status='complete', created_at=self.now,
                            blocks=json.dumps(blocks or [], ensure_ascii=False))
            s.add(row)
            s.flush()
            return row.id

    def _letter(self, *, kind='letter', pet_id=None, turn_id=None, created_at=None,
                title='电量观察'):
        self._ensure_enabled()
        with db.session_scope() as s:
            pet = pet_id or self.pet_id
            task = AgentTask(pet_id=pet, task_key='task-%d' % pet, current_version=1,
                             status='observing', timezone='Asia/Shanghai',
                             deadline_at=self.now + timedelta(days=1), effective_from=self.now,
                             paused_intervals='[]', created_at=self.now, updated_at=self.now)
            s.add(task)
            s.flush()
            s.add(AgentTaskPlan(task_id=task.id, version=1,
                                payload=json.dumps({'title': title, 'kind': 'fact_watch'},
                                                   ensure_ascii=False),
                                created_at=self.now))
            delivery = AgentTaskDelivery(task_id=task.id, plan_version=1, kind=kind,
                                         instance_key='letter:v1', turn_id=turn_id,
                                         payload=json.dumps({'text': '这段时间的电量观察结果…'},
                                                            ensure_ascii=False),
                                         created_at=created_at or self.now)
            s.add(delivery)
            s.flush()
            return delivery.id

    def _items(self, pet_id=None):
        with db.session_scope() as s:
            stmt = select(AgentInboxItem).order_by(AgentInboxItem.id)
            if pet_id:
                stmt = stmt.where(AgentInboxItem.pet_id == pet_id)
            rows = s.execute(stmt).scalars().all()
            return [(row.source_kind, row.event_key, row.id) for row in rows]

    # ── A1：四类来源各一条，重复投影不重复 ────────────────────────────────
    def test_four_source_kinds_produce_one_item_each(self):
        device_topic = self._topic('device', 'battery_low', 'dev-w12')
        self._turn('care:event:%d:open' % device_topic, topic_id=device_topic)
        care_topic = self._topic('observation', 'walk_volume', 'anchor-1',
                                 evidence=[{'kind': 'review', 'window': '2026-11-22'}])
        self._turn('care:review:daily:%d:2026-11-22' % self.pet_id)
        learning_topic = self._topic('learning', 'learning_ready', 'actions:v1', severity='info')
        self._turn('care:event:%d:open' % learning_topic, topic_id=learning_topic)
        letter_turn = self._turn('task-turn')
        self._letter(turn_id=letter_turn)

        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        kinds = sorted(kind for kind, _key, _id in self._items())
        self.assertEqual(kinds, ['care', 'device_fact', 'learning', 'reply'])
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            inbox.project_user(s, self.uid)
        self.assertEqual(len(self._items()), 4)

    def test_escalation_creates_new_item(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12', severity='warning')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, topic_id)
            topic.severity = 'critical'
            topic.status = 'ongoing'
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        keys = [key for _kind, key, _id in self._items()]
        self.assertEqual(len(keys), 2)
        self.assertTrue(any(key.endswith(':critical') for key in keys))

    # ── 复核意见 1：精确的来源→回合身份 ───────────────────────────────────
    def test_exact_turn_identity_for_event_phases(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        open_turn = self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertEqual(row.turn_id, open_turn)
            self.assertEqual(row.published, 1)

    def test_exact_turn_identity_for_review_day_and_week(self):
        self._topic('observation', 'walk_volume', 'anchor-d',
                    evidence=[{'kind': 'review', 'window': '2026-11-23'}])
        daily_turn = self._turn('care:review:daily:%d:2026-11-23' % self.pet_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertEqual(row.turn_id, daily_turn)
            self.assertEqual(row.published, 1)
        weekly_turn = self._turn('care:review:weekly:%d:2026-11-23' % self.pet_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertEqual(row.turn_id, weekly_turn)

    def test_prefix_only_request_id_is_not_matched(self):
        """旧实现用 LIKE 前缀匹配；真实 key 必须精确到阶段。"""
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        self._turn('care:event:%d:open-extra' % topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertIsNone(row.turn_id)
            self.assertEqual(row.published, 0)

    # ── 复核意见 2：发布晚于水位 → 富化补齐，不复活已读 ──────────────────
    def test_enrichment_fills_publication_after_watermark_moved(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)     # 先推进水位（此时 W09 还没发布）
        item_id = self._items()[0][2]
        with db.session_scope() as s:
            row = s.get(AgentInboxItem, item_id)
            self.assertEqual(row.published, 0)
            self.assertIsNone(row.turn_id)
            row.read_at = utc_now()              # 已读
        turn_id = self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)     # 再次投影 → 富化
        with db.session_scope() as s:
            row = s.get(AgentInboxItem, item_id)
            self.assertEqual(row.published, 1)
            self.assertEqual(row.turn_id, turn_id)
            self.assertIsNotNone(row.read_at)    # 已读不被复活
            self.assertEqual(row.previewable, 1)

    def test_enrichment_covers_the_newest_items(self):
        """富化取最新的一批：条目累计超过一批后，新条目的迟到发布仍能被富化到。"""
        self._ensure_enabled()
        with db.session_scope() as s:
            # 已坐满一批历史条目（学习完成，不参与预览与推送）
            s.add_all([AgentInboxItem(
                user_id=self.uid, pet_id=self.pet_id, source_kind='learning',
                event_key='filler:%d' % n, source_ref='{}', priority=9, previewable=0,
                published=1, status='available', backfilled=0,
                created_at=self.now, updated_at=self.now)
                for n in range(inbox.ENRICH_LIMIT)])
        topic_id = self._topic('device', 'battery_low', 'dev-w12-latest')
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            item_id = s.execute(select(AgentInboxItem.id).where(
                AgentInboxItem.pet_id == self.pet_id,
                AgentInboxItem.source_kind == 'device_fact')).scalar_one()
            self.assertEqual(s.get(AgentInboxItem, item_id).published, 0)
        turn_id = self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            row = s.get(AgentInboxItem, item_id)
            self.assertEqual(row.published, 1, '最新一批里的新条目被富化')
            self.assertEqual(row.turn_id, turn_id)

    def test_backfill_never_becomes_push_candidate(self):
        self._auto_enable = False
        topic_id = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertEqual(row.backfilled, 1)
            self.assertEqual(inbox.push_candidates(s, self.uid), [])  # 历史补入不追溯推送

    # ── 中心策略：推送候选（W13 D7） ──────────────────────────────────────
    # ── D7（已确认）：关键设备告警 = warning/critical；info 永远只在收件箱 ──
    def test_d7_info_device_alert_is_inbox_only(self):
        topic_id = self._topic('device', 'data_stale', 'dev-w12', severity='info')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertEqual(row.previewable, 0)              # 不进今日预览
            self.assertEqual(row.published, 1)
            self.assertEqual(inbox.push_candidates(s, self.uid), [])  # 不是推送候选
            self.assertIsNone(inbox.preview_item(s, self.uid))  # 预览为空
            self.assertEqual(inbox.unread_count(s, self.uid), 1)  # 但仍在收件箱计数里

    def test_d7_warning_and_critical_open_alerts_are_key(self):
        warn_id = self._topic('device', 'battery_low', 'dev-w12', severity='warning')
        self._turn('care:event:%d:open' % warn_id, topic_id=warn_id)
        crit_id = self._topic('device', 'readings_invalid', 'dev-w12-2', severity='critical')
        self._turn('care:event:%d:open' % crit_id, topic_id=crit_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            preview = inbox.preview_item(s, self.uid)
            self.assertIsNotNone(preview)
            self.assertEqual(preview.source_kind, 'device_fact')
            candidates = inbox.push_candidates(s, self.uid)
            self.assertEqual({c.source_kind for c in candidates}, {'device_fact'})
            self.assertEqual(len(candidates), 2)

    def test_d7_closed_alert_leaves_key_priority(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, topic_id)
            topic.status = 'recovered'
            topic.recovered_at = self.now
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            self.assertIsNone(inbox.preview_item(s, self.uid))
            self.assertEqual(inbox.push_candidates(s, self.uid), [])

    # ── 深链锚点回合：精确取回（不依赖会话分页/话题过滤） ─────────────────
    def test_anchor_turn_is_fetchable_regardless_of_page(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        turn_id = self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        body = self.client.get('/api/app/agent/inbox/turn/%d' % turn_id,
                               headers={HEADER: self.token}).get_json()
        self.assertEqual(body['turn']['id'], turn_id)
        self.assertEqual(body['turn']['conversationId'], self._conversations[self.pet_id])
        # 跨账号取回一律 404
        self.assertEqual(self.client.get('/api/app/agent/inbox/turn/%d' % turn_id,
                                         headers={HEADER: self.other_token}).status_code, 404)
        self.assertEqual(self.client.get('/api/app/agent/inbox/turn/999999',
                                         headers={HEADER: self.token}).status_code, 404)

    def test_push_candidates_for_device_and_reply(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        letter_turn = self._turn('task-turn')
        self._letter(turn_id=letter_turn)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            kinds = {row.source_kind for row in inbox.push_candidates(s, self.uid)}
            self.assertEqual(kinds, {'device_fact', 'reply'})

    def test_quiet_mode_does_not_block_device_or_reply_push(self):
        """安静档只约束主动会话，设备告警与到期回信的推送资格不受影响。"""
        from petsense.agent import settings as settings_service
        with db.session_scope() as s:
            settings_service.persist(s, self.uid, [('proactivity', 0)])
        topic_id = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        letter_turn = self._turn('task-turn')
        self._letter(turn_id=letter_turn)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            kinds = {row.source_kind for row in inbox.push_candidates(s, self.uid)}
            self.assertEqual(kinds, {'device_fact', 'reply'})

    def test_ordinary_care_and_learning_are_never_push_candidates(self):
        self._topic('observation', 'walk_volume', 'anchor-1',
                    evidence=[{'kind': 'review', 'window': '2026-11-22'}])
        self._turn('care:review:daily:%d:2026-11-22' % self.pet_id)
        learning_topic = self._topic('learning', 'learning_ready', 'actions:v1', severity='info')
        self._turn('care:event:%d:open' % learning_topic, topic_id=learning_topic)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            self.assertEqual(inbox.push_candidates(s, self.uid), [])

    def test_personal_high_requires_frozen_evidence_and_explicit_high(self):
        self._topic('observation', 'walk_volume', 'anchor-1',
                    evidence=[{'kind': 'review', 'window': '2026-11-22'}])
        self._turn('care:review:daily:%d:2026-11-22' % self.pet_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        item_id = self._items()[0][2]
        with db.session_scope() as s:
            inbox.mark_personal_eligibility(s, self.uid, item_id, frozen_evidence=True,
                                            delivery_approved=True)
            row = s.get(AgentInboxItem, item_id)
            self.assertEqual(row.high_relevance, 0)     # 缺失相关性不得按 high 处理
            self.assertEqual(row.push_candidate, 0)
        with db.session_scope() as s:
            inbox.mark_personal_eligibility(s, self.uid, item_id, relevance='high')
            row = s.get(AgentInboxItem, item_id)
            self.assertEqual(row.high_relevance, 1)
            self.assertEqual(row.push_candidate, 1)
            self.assertEqual([c.id for c in inbox.push_candidates(s, self.uid)], [item_id])

    # ── D5：启用只补当下仍开放的设备告警 ────────────────────────────────
    def test_enablement_backfills_only_open_device_alerts(self):
        self._auto_enable = False
        closed_topic = self._topic('device', 'data_stale', 'dev-w12', status='recovered',
                                   severity='info', opened_at=self.now - timedelta(days=2))
        open_topic = self._topic('device', 'battery_low', 'dev-w12', severity='warning',
                                 opened_at=self.now - timedelta(days=2))
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        keys = [key for _k, key, _i in self._items()]
        self.assertEqual(len(keys), 1)
        self.assertIn('topic:%d:warning' % open_topic, keys)
        self.assertNotIn('topic:%d:info' % closed_topic, keys)
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertEqual(row.backfilled, 1)

    def test_closed_device_alert_refreshes_same_row_without_reviving_unread(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        item_id = self._items()[0][2]
        with db.session_scope() as s:
            s.get(AgentInboxItem, item_id).read_at = utc_now()
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, topic_id)
            topic.status = 'recovered'
            topic.recovered_at = self.now
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            rows = s.execute(select(AgentInboxItem)).scalars().all()
            self.assertEqual(len(rows), 1)            # 同一行刷新，不新增
            self.assertIsNotNone(rows[0].read_at)     # 未读不复活
            self.assertEqual(rows[0].previewable, 0)  # 关闭后退出关键优先级

    def test_other_sources_enter_only_after_enablement(self):
        self._auto_enable = False
        care_topic = self._topic('observation', 'walk_volume', 'anchor-old',
                                 opened_at=self.now - timedelta(days=3))
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        self.assertEqual(self._items(), [])
        later = self._topic('observation', 'walk_volume', 'anchor-new',
                            opened_at=utc_now() + timedelta(seconds=1))
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        keys = [key for _k, key, _i in self._items()]
        self.assertEqual(keys, ['topic:%d' % later])
        self.assertNotIn('topic:%d' % care_topic, keys)

    def test_projection_watermark_survives_restart(self):
        self._topic('device', 'battery_low', 'dev-w12')
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            state = s.get(AgentInboxState, self.pet_id)
            self.assertIsNotNone(state)
            self.assertGreater(state.care_watermark, 0)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        self.assertEqual(len(self._items()), 1)

    # ── A2：预览优先级与计数 ──────────────────────────────────────────────
    def test_preview_priority_and_unread_count(self):
        device_topic = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % device_topic, topic_id=device_topic)
        self._topic('learning', 'learning_ready', 'actions:v1', severity='info')
        letter_turn = self._turn('task-turn')
        self._letter(turn_id=letter_turn)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            count = inbox.unread_count(s, self.uid)
            preview = inbox.preview_item(s, self.uid)
            self.assertEqual(count, 3)
            self.assertIsNotNone(preview)
            self.assertEqual(preview.source_kind, 'device_fact')
            learning = s.execute(select(AgentInboxItem).where(
                AgentInboxItem.source_kind == 'learning')).scalar_one()
            self.assertEqual(learning.previewable, 0)

    def test_device_closed_leaves_critical_priority(self):
        device_topic = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % device_topic, topic_id=device_topic)
        letter_turn = self._turn('task-turn')
        self._letter(turn_id=letter_turn)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, device_topic)
            topic.status = 'recovered'
            topic.recovered_at = self.now
        with db.session_scope() as s:
            preview = inbox.preview_item(s, self.uid)
            self.assertIsNotNone(preview)
            self.assertEqual(preview.source_kind, 'reply')

    def test_quiet_mode_care_topic_is_inbox_only(self):
        self._topic('observation', 'walk_volume', 'anchor-1')
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        with db.session_scope() as s:
            preview = inbox.preview_item(s, self.uid)
            self.assertIsNone(preview)
            self.assertEqual(inbox.unread_count(s, self.uid), 1)
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertEqual(row.published, 0)

    # ── A3：已读语义 ──────────────────────────────────────────────────────
    def test_mark_read_is_idempotent_and_does_not_close_alert(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        item_id = self._items()[0][2]
        first = self.client.post('/api/app/agent/inbox/%d/read' % item_id,
                                 headers={HEADER: self.token})
        self.assertEqual(first.status_code, 200)
        second = self.client.post('/api/app/agent/inbox/%d/read' % item_id,
                                  headers={HEADER: self.token})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()['unreadCount'], 0)
        with db.session_scope() as s:
            self.assertEqual(s.get(AgentCareTopic, topic_id).status, 'open')
            self.assertIsNotNone(s.get(AgentInboxItem, item_id).read_at)

    def test_mark_all_read_respects_visible_watermark(self):
        self._topic('device', 'battery_low', 'dev-w12')
        self._topic('device', 'readings_invalid', 'dev-w12-2')
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        items = self._items()
        self.assertEqual(len(items), 2)
        first_id = items[0][2]
        result = self.client.post('/api/app/agent/inbox/read-all',
                                  headers={HEADER: self.token},
                                  json={'throughId': first_id})
        self.assertEqual(result.status_code, 200)
        body = result.get_json()
        self.assertEqual(body['updated'], 1)
        self.assertEqual(body['unreadCount'], 1)

    def test_foreign_pet_filter_is_rejected_before_writes(self):
        """他人的宠物过滤必须被拒绝，不能落到「标记本人全部宠物已读」。"""
        self._topic('device', 'battery_low', 'dev-w12')
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        self.assertEqual(len(self._items()), 1)
        result = self.client.post('/api/app/agent/inbox/read-all?pet=%d' % self.other_pet_id,
                                  headers={HEADER: self.token})
        self.assertEqual(result.status_code, 404)
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertIsNone(row.read_at)          # 没有误标其他宠物
        listing = self.client.get('/api/app/agent/inbox?pet=%d' % self.other_pet_id,
                                  headers={HEADER: self.token})
        self.assertEqual(listing.status_code, 404)
        bad = self.client.get('/api/app/agent/inbox?pet=abc', headers={HEADER: self.token})
        self.assertEqual(bad.status_code, 400)

    def test_same_owner_multiple_pets_are_separated(self):
        self._topic('device', 'battery_low', 'dev-w12', pet_id=self.pet_id)
        self._topic('device', 'battery_low', 'dev-w12-2', pet_id=self.pet2_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        self.assertEqual(len(self._items(self.pet_id)), 1)
        self.assertEqual(len(self._items(self.pet2_id)), 1)
        body = self.client.get('/api/app/agent/inbox?pet=%d' % self.pet2_id,
                               headers={HEADER: self.token}).get_json()
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(body['items'][0]['petId'], self.pet2_id)
        self.assertEqual(body['unreadCount'], 1)

    def test_cross_account_item_is_not_visible_or_readable(self):
        with db.session_scope() as s:
            pet = s.get(Pet, self.other_pet_id)
            topic = AgentCareTopic(pet_id=pet.id, kind='device', category='battery_low',
                                   anchor='dev-other', instance=1, status='open',
                                   severity='warning', title='t', detail='d', evidence='[]',
                                   delivered=0, opened_at=self.now, updated_at=self.now)
            s.add(topic)
            s.flush()
            inbox.project_pet(s, pet, now=self.now)
            other_item = s.execute(select(AgentInboxItem).where(
                AgentInboxItem.user_id == self.other_uid)).scalar_one()
            other_item_id = other_item.id
        detail = self.client.get('/api/app/agent/inbox/%d' % other_item_id,
                                 headers={HEADER: self.token})
        self.assertEqual(detail.status_code, 404)
        read = self.client.post('/api/app/agent/inbox/%d/read' % other_item_id,
                                headers={HEADER: self.token})
        self.assertEqual(read.status_code, 404)
        resolve = self.client.get('/api/app/agent/inbox/resolve/%d' % other_item_id,
                                  headers={HEADER: self.token})
        self.assertEqual(resolve.status_code, 404)
        with db.session_scope() as s:
            self.assertIsNone(s.get(AgentInboxItem, other_item_id).read_at)
        listing = self.client.get('/api/app/agent/inbox', headers={HEADER: self.token})
        self.assertEqual(listing.get_json()['items'], [])

    def test_unauthenticated_request_is_rejected(self):
        self.assertEqual(self.client.get('/api/app/agent/inbox').status_code, 401)

    # ── A4：深链解析、锚点与「打开不产生新回合」 ─────────────────────────
    def test_resolve_target_exposes_anchor_without_marking_read(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        turn_id = self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        letter_turn = self._turn('task-turn')
        delivery_id = self._letter(turn_id=letter_turn)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        kinds = {kind: item_id for kind, _key, item_id in self._items()}
        body = self.client.get('/api/app/agent/inbox/resolve/%d' % kinds['device_fact'],
                               headers={HEADER: self.token}).get_json()
        target = body['target']
        self.assertEqual(target['route'], '/agent')
        self.assertEqual(target['anchor'], turn_id)
        self.assertEqual(target['params']['topicId'], str(topic_id))
        self.assertEqual(target['params']['turnId'], str(turn_id))
        # 深链参数带锚点所在的会话（W16）：客户端先切到它再定位
        self.assertEqual(target['params']['conversationId'], str(self._conversations[self.pet_id]))
        # 纯读解析不改变未读状态（冷启动先解析、后展示再标已读）
        self.assertIsNone(body['item']['readAt'])
        reply = self.client.get('/api/app/agent/inbox/resolve/%d' % kinds['reply'],
                                headers={HEADER: self.token}).get_json()['target']
        self.assertEqual(reply['params']['deliveryId'], str(delivery_id))
        self.assertEqual(reply['params']['conversationId'], str(self._conversations[self.pet_id]))
        self.assertEqual(reply['anchor'], letter_turn)

    def test_deleted_conversation_hides_turn_and_reply(self):
        """会话被主人删除后：那条回合取不回，关怀条目不再给会话，回信条目标记来源不可用。"""
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        turn_id = self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        letter_turn = self._turn('task-turn')
        self._letter(turn_id=letter_turn)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
            conversation_id = s.get(AgentTurn, turn_id).conversation_id
            conversation = s.get(AgentConversation, conversation_id)
            conversation.deleted_at = self.now
        kinds = {kind: item_id for kind, _key, item_id in self._items()}
        self.assertEqual(self.client.get('/api/app/agent/inbox/turn/%d' % turn_id,
                                         headers={HEADER: self.token}).status_code, 404)
        care = self.client.get('/api/app/agent/inbox/resolve/%d' % kinds['device_fact'],
                               headers={HEADER: self.token}).get_json()['target']
        self.assertTrue(care['available'])           # 主题本身还在：按安静主题的方式展示主题卡
        self.assertNotIn('conversationId', care['params'])
        self.assertEqual(care['params']['turnId'], str(turn_id))
        reply = self.client.get('/api/app/agent/inbox/resolve/%d' % kinds['reply'],
                                headers={HEADER: self.token}).get_json()
        self.assertFalse(reply['target']['available'])
        self.assertEqual(reply['target']['reason'], 'conversation_deleted')

    def test_failed_target_keeps_unread(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        item_id = self._items()[0][2]
        with db.session_scope() as s:
            s.delete(s.get(AgentCareTopic, topic_id))
        body = self.client.get('/api/app/agent/inbox/resolve/%d' % item_id,
                               headers={HEADER: self.token}).get_json()
        self.assertFalse(body['target']['available'])
        self.assertEqual(body['target']['reason'], 'source_unavailable')
        self.assertIsNone(body['item']['readAt'])       # 未读保持
        with db.session_scope() as s:
            self.assertIsNone(s.get(AgentInboxItem, item_id).read_at)

    def test_rebind_closed_topic_is_out_of_scope_without_leaking_body(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12', detail='电量为 3%',
                                title='项圈电量偏低')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        item_id = self._items()[0][2]
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, topic_id)
            topic.status = 'closed'
            topic.closed_reason = 'rebind'
        body = self.client.get('/api/app/agent/inbox/%d' % item_id,
                               headers={HEADER: self.token}).get_json()
        self.assertEqual(body['item']['source']['status'], 'out_of_scope')
        self.assertEqual(body['item']['source']['reason'], 'rebind')
        self.assertEqual(body['item']['title'], '')      # 不泄露原绑定正文
        self.assertEqual(body['item']['summary'], '')
        self.assertFalse(body['item']['target']['available'])

    def test_source_must_resolve_under_same_pet(self):
        """来源被换到别的宠物下时不出内容。"""
        topic_id = self._topic('device', 'battery_low', 'dev-w12', detail='电量为 3%')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        item_id = self._items()[0][2]
        with db.session_scope() as s:
            s.get(AgentCareTopic, topic_id).pet_id = self.pet2_id
        body = self.client.get('/api/app/agent/inbox/%d' % item_id,
                               headers={HEADER: self.token}).get_json()
        self.assertEqual(body['item']['source']['status'], 'out_of_scope')
        self.assertEqual(body['item']['source']['reason'], 'pet_mismatch')
        self.assertEqual(body['item']['summary'], '')

    def test_opening_item_creates_no_new_turn(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        item_id = self._items()[0][2]
        with db.session_scope() as s:
            before = len(s.execute(select(AgentTurn)).scalars().all())
        self.client.get('/api/app/agent/inbox/%d' % item_id, headers={HEADER: self.token})
        self.client.get('/api/app/agent/inbox/resolve/%d' % item_id, headers={HEADER: self.token})
        with db.session_scope() as s:
            after = len(s.execute(select(AgentTurn)).scalars().all())
        self.assertEqual(before, after)

    # ── 分页与作业 ────────────────────────────────────────────────────────
    def test_pagination_cursor_is_stable(self):
        for index in range(3):
            self._topic('device', 'battery_low', 'dev-%d' % index)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        body = self.client.get('/api/app/agent/inbox?limit=2', headers={HEADER: self.token}).get_json()
        self.assertEqual(len(body['items']), 2)
        self.assertIsNotNone(body['nextCursor'])
        next_body = self.client.get('/api/app/agent/inbox?limit=2&before=%d' % body['nextCursor'],
                                    headers={HEADER: self.token}).get_json()
        self.assertEqual(len(next_body['items']), 1)
        self.assertIsNone(next_body['nextCursor'])
        ids = [item['id'] for item in body['items']] + [item['id'] for item in next_body['items']]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_projection_job_creates_items(self):
        self._topic('device', 'battery_low', 'dev-w12')
        inbox_jobs.register()
        result = inbox_jobs.handle_project({}, self.now)
        self.assertIn('next_run_at', result)
        self.assertEqual(len(self._items()), 1)

    def test_list_endpoint_reports_capability_and_preview(self):
        topic_id = self._topic('device', 'battery_low', 'dev-w12', severity='critical')
        self._turn('care:event:%d:open' % topic_id, topic_id=topic_id)
        with db.session_scope() as s:
            inbox.project_user(s, self.uid)
        body = self.client.get('/api/app/agent/inbox', headers={HEADER: self.token}).get_json()
        self.assertEqual(body['unreadCount'], 1)
        self.assertIsNotNone(body['preview'])
        self.assertTrue(body['capability']['serverInbox'])
        self.assertIn('battery_low', body['capability']['managedRules'])
        # 列表里的标题/摘要来自来源水合（索引表不复制正文）
        self.assertEqual(body['items'][0]['title'], 'battery_low')
        with db.session_scope() as s:
            row = s.execute(select(AgentInboxItem)).scalar_one()
            self.assertFalse(hasattr(row, 'title'))


if __name__ == '__main__':
    unittest.main()
