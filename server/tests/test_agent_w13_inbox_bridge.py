"""W12 → W13 集成测试：真实投影缝 + 真实当前来源解析器 + 假 Expo 服务方。

与 `test_agent_w13.py`（W13 单元合同）互补：这里不 mock W12——收件箱条目由
`inbox.project_*` 真实投影产生，「当前来源」解析器由 `inbox.install_push_bridge()`
真实安装，只有 Expo Push Service 用假 provider（不接触真实服务方）。覆盖：

- 条目是推送候选时入队：创建时、每轮富化与资格写入后都会重新递交，W13 按安装实例
  去重（恰好一次；重复投影不重复入队；递交从不改写 W12 的候选位）
- 发布晚于投影：富化补齐回合身份后那次投影入队（此前不建行）
- 启用补入（backfilled）与超出新事件窗口的历史条目不入队
- 闸门关闭不建行，开闸后的下一轮递交补上；开启推送之前发生的事件不补推
- 发送前按真实当前来源复核：告警关闭 → not_push_candidate；宠物转走 →
  pet_scope_changed；来源缺失 → source_unavailable
- 到期回信路径：已交付回信入队并发送
- W11 个人变化路径：`mark_personal_eligibility` 三项齐备才入队；安静档在发送前抑制
  且为终态
"""
import json
import os
import sys
import unittest
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import fresh_db, new_conversation
from sqlalchemy import select

from petsense import db
from petsense.agent import inbox, inbox_eligibility, push, push_provider
from petsense.db import iso_z, utc_now
from petsense.db.models import Pet, User
from petsense.db.models.agent import (AgentCareTopic, AgentInboxItem, AgentTask,
                                       AgentTaskDelivery, AgentTurn, CompanionSettings)
from petsense.db.models.push import PushDelivery, PushInstallation

INSTALLATION_A = 'install-aaaaaaaaaaaa'
TOKEN_A = 'ExponentPushToken[aaaa1111]'


class FakeExpoProvider:
    """假 Expo 服务方：记录发送，返回可预置票据（不接触真实服务方）。"""

    def __init__(self):
        self.sent = []
        self.tickets = []

    def send_messages(self, messages):
        self.sent.append(list(messages))
        return [dict(self.tickets.pop(0)) if self.tickets else {'status': 'ok', 'id': 't-1'}
                for _ in messages]

    def get_receipts(self, ticket_ids):
        return {}


class W13InboxBridgeTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.provider = FakeExpoProvider()
        self.now = utc_now()
        # 真实解析器：发送前复核读 W12 当前来源（不再注入假 resolver）
        inbox.install_push_bridge()
        self.addCleanup(push.clear_inbox_source)
        with db.session_scope() as s:
            user = User(username='bridge', display_name='bridge', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(user)
            s.flush()
            self.uid = user.id
            other = User(username='bridge-other', display_name='other', password_hash='x',
                         salt='s', avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(other)
            s.flush()
            self.other_uid = other.id
            pet = Pet(user_id=user.id, name='豆豆', species='dog', device_id='dev-bridge',
                      created_at=self.now)
            s.add(pet)
            s.flush()
            self.pet_id = pet.id
            s.add(CompanionSettings(user_id=user.id, assistant_name='助手', owner_title=None,
                                    verbosity='concise', proactivity=1, updated_at=self.now))
        self.conversation = new_conversation(self.pet_id)  # W16：回合与委托必须属于某个会话
        self._enable_push()

    # ── 夹具 ────────────────────────────────────────────────────────────────

    @contextmanager
    def _gate_on(self):
        with patch.dict(os.environ, {'PETSENSE_PUSH_IOS_ENABLED': '1'}):
            yield

    def _enable_push(self):
        with db.session_scope() as s:
            push.set_user_enabled(s, self.uid, True, now=self.now)
            push.register_installation(s, self.uid, INSTALLATION_A, 'ios', TOKEN_A,
                                       permission_status='granted', now=self.now)

    def _enable_inbox(self):
        """首次投影 = 启用水位（部署语义）；启用前已开放的设备告警按 backfilled 补入。"""
        with db.session_scope() as s:
            inbox.project_user(s, self.uid, now=self.now)

    def _topic(self, kind='device', category='battery_low', *, status='open', severity='warning',
               evidence=None, anchor='anchor-1'):
        with db.session_scope() as s:
            row = AgentCareTopic(pet_id=self.pet_id, kind=kind, category=category,
                                 anchor=anchor, instance=1, status=status,
                                 severity=severity, title=category, detail='',
                                 evidence=json.dumps(evidence or [], ensure_ascii=False),
                                 delivered=0, opened_at=self.now, updated_at=self.now)
            s.add(row)
            s.flush()
            return row.id

    def _topic_turn(self, topic_id, phase='open'):
        """W09 事件交付回合（request id 与 care_jobs 真实 key 一致）。"""
        with db.session_scope() as s:
            row = AgentTurn(pet_id=self.pet_id, conversation_id=self.conversation,
                            request_id='care:event:%d:%s' % (topic_id, phase),
                            text='', target='assistant', topic_id=topic_id, reply_to=None,
                            sources='[]', speaker='assistant', reply='已提醒', status='complete',
                            created_at=self.now)
            s.add(row)
            s.flush()
            return row.id

    def _review_turn(self, window):
        """W09 回顾交付回合（request id 与 care_jobs 真实 key 一致）。"""
        with db.session_scope() as s:
            row = AgentTurn(pet_id=self.pet_id, conversation_id=self.conversation,
                            request_id='care:review:daily:%d:%s' % (self.pet_id, window),
                            text='', target='assistant', topic_id=None, reply_to=None,
                            sources='[]', speaker='assistant', reply='回顾', status='complete',
                            created_at=self.now)
            s.add(row)
            s.flush()
            return row.id

    def _letter(self, *, with_turn=True):
        """W08 已交付到期回信（turn_id 落在交付行上）。"""
        self._letter_seq = getattr(self, '_letter_seq', 0) + 1
        with db.session_scope() as s:
            task = AgentTask(pet_id=self.pet_id, conversation_id=self.conversation,
                             task_key='task-%d' % self._letter_seq,
                             current_version=1, status='observing', timezone='Asia/Shanghai',
                             deadline_at=self.now + timedelta(days=1),
                             effective_from=self.now, paused_intervals='[]',
                             created_at=self.now, updated_at=self.now)
            s.add(task)
            s.flush()
            turn_id = None
            if with_turn:
                turn = AgentTurn(pet_id=self.pet_id, conversation_id=self.conversation,
                                 request_id='task:letter:%d' % task.id,
                                 text='', target='assistant', topic_id=None, reply_to=None,
                                 sources='[]', speaker='assistant', reply='回信已到',
                                 status='complete', created_at=self.now)
                s.add(turn)
                s.flush()
                turn_id = turn.id
            delivery = AgentTaskDelivery(task_id=task.id, plan_version=1, kind='letter',
                                         instance_key='letter:v1', turn_id=turn_id,
                                         payload='{"text": "该复查了"}', created_at=self.now)
            s.add(delivery)
            s.flush()
            return delivery.id

    def _project(self):
        """跑一轮真实投影（闸门开启时当前候选条目会在同一事务里重新递交）。"""
        with self._gate_on():
            with db.session_scope() as s:
                return inbox.project_user(s, self.uid, now=self.now)

    def _items(self):
        with db.session_scope() as s:
            return s.execute(select(AgentInboxItem).order_by(AgentInboxItem.id)).scalars().all()

    def _deliveries(self):
        with db.session_scope() as s:
            return s.execute(select(PushDelivery).order_by(PushDelivery.id)).scalars().all()

    def _dispatch(self, delivery_id, now=None):
        # 投递行由 publish 缝以真实 utc_now 排期，用真实时钟派发（不能早于排期）
        with self._gate_on():
            return push.dispatch_delivery(delivery_id, now=now or utc_now(),
                                          provider=self.provider)

    # ── 首次合格入队：恰好一次 ──────────────────────────────────────────────

    def test_first_qualification_enqueues_exactly_once(self):
        """已发布的 warning 设备告警：首次投影入队（每安装一行），重复投影不重复。"""
        self._enable_inbox()          # 启用先于来源：正常入箱（非 backfill）
        topic_id = self._topic()
        self._topic_turn(topic_id)
        created = self._project()
        self.assertEqual(created, 1, '设备告警入箱')
        rows = self._deliveries()
        self.assertEqual(len(rows), 1, '首次合格：入队一行')
        items = self._items()
        self.assertEqual(rows[0].inbox_item_id, str(items[0].id), '稳定收件箱 ID 进投递行')
        self.assertEqual(rows[0].category, 'device_fact', '分类沿用 W12 来源类型')
        # 重复投影/富化：不重复入队
        self.assertEqual(self._project(), 0)
        self.assertEqual(self._project(), 0)
        self.assertEqual(len(self._deliveries()), 1)
        # 投递：假服务方收到载荷，稳定收件箱 ID 与通用文案都在
        result = self._dispatch(rows[0].id)
        self.assertEqual(result['state'], push.STATE_SENT)
        self.assertEqual(len(self.provider.sent), 1)
        message = self.provider.sent[0][0]
        self.assertEqual(message['data']['inboxItemId'], str(items[0].id))
        self.assertEqual(message['data']['category'], 'device_fact')
        self.assertNotIn('豆豆', message['body'], '锁屏不出现宠物名')

    def test_published_after_projection_enqueues_on_that_projection(self):
        """投影早于 W09 交付：交付后那次投影的富化让条目首次合格并入队，此前不建行。"""
        self._enable_inbox()
        topic_id = self._topic()
        self._project()
        self.assertEqual(self._deliveries(), [], '未发布（无交付回合）不入队')
        self._topic_turn(topic_id)
        self._project()
        rows = self._deliveries()
        self.assertEqual(len(rows), 1, '富化发现已发布 → 首次合格入队')

    def test_backfill_and_stale_items_never_enqueue(self):
        """启用补入与超窗历史条目只进收件箱，不产生投递行。"""
        topic_id = self._topic()      # 启用前已开放的告警
        self._topic_turn(topic_id)
        self._enable_inbox()          # 首次投影 → backfilled 条目
        self.assertEqual(self._project(), 0)
        items = self._items()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].backfilled)
        self.assertEqual(self._deliveries(), [], '历史补入不追溯推送')
        # 非补入条目但已超出新事件窗口（24h）→ 同样不入队
        with db.session_scope() as s:
            item = s.execute(select(AgentInboxItem)).scalars().one()
            item.backfilled = 0
            item.push_candidate = 0          # 模拟「尚未合格」的旧条目
            item.created_at = self.now - timedelta(seconds=push.PUSH_EVENT_MAX_AGE_S + 120)
            s.flush()
        self._project()
        self.assertEqual(self._deliveries(), [], '超出新事件窗口的历史条目不补推')

    # ── 发送前按真实当前来源复核 ────────────────────────────────────────────

    def _enqueued_delivery(self):
        """造一条已发布告警并完成入队，返回（投递行, 主题 id）。"""
        self._enable_inbox()
        topic_id = self._topic()
        self._topic_turn(topic_id)
        self._project()
        rows = self._deliveries()
        assert len(rows) == 1, '夹具应产生一条待发件'
        return rows[0], topic_id

    def test_closed_alert_suppressed_by_real_source_recheck(self):
        delivery, topic_id = self._enqueued_delivery()
        with db.session_scope() as s:
            topic = s.get(AgentCareTopic, topic_id)
            topic.status = 'closed'
            topic.updated_at = self.now
            s.flush()
        result = self._dispatch(delivery.id)
        self.assertEqual(result['state'], push.STATE_SUPPRESSED)
        self.assertEqual(result['reason'], 'not_push_candidate', '告警已关闭 → 中心策略翻不可推')
        self.assertEqual(self.provider.sent, [])

    def test_transferred_pet_suppressed_by_real_source_recheck(self):
        delivery, _topic_id = self._enqueued_delivery()
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet_id)
            pet.user_id = self.other_uid
            s.flush()
        result = self._dispatch(delivery.id)
        self.assertEqual(result['state'], push.STATE_SUPPRESSED)
        self.assertEqual(result['reason'], 'pet_scope_changed', '宠物已转走 → 范围复核拦截')
        self.assertEqual(self.provider.sent, [])

    def test_missing_source_suppressed(self):
        """条目被删（来源不可达）→ 保守不发送。"""
        delivery, _topic_id = self._enqueued_delivery()
        with db.session_scope() as s:
            item = s.execute(select(AgentInboxItem)).scalars().one()
            s.delete(item)
            s.flush()
        result = self._dispatch(delivery.id)
        self.assertEqual(result['state'], push.STATE_SUPPRESSED)
        self.assertEqual(result['reason'], 'source_unavailable')

    def test_resolver_contract_shape(self):
        """解析器输出覆盖 SOURCE_CONTRACT 全部键；条目 ID 以字符串传入也可解析。"""
        delivery, topic_id = self._enqueued_delivery()
        self._topic_turn(topic_id, phase='escalate')  # 无关数据，确认按精确 id 匹配
        with db.session_scope() as s:
            source = push.SOURCE_RESOLVER(s, delivery.inbox_item_id)  # 字符串 ID
        self.assertIsNotNone(source)
        for key in push.SOURCE_CONTRACT:
            self.assertIn(key, source, '缺少契约键 %s' % key)
        self.assertEqual(source['sourceKind'], 'device_fact')
        self.assertEqual(source['severity'], 'warning')
        self.assertTrue(source['pushCandidate'])
        self.assertEqual(source['userId'], self.uid)
        self.assertEqual(source['petOwnerId'], self.uid)
        self.assertIsNone(push.SOURCE_RESOLVER(db.Session(), '999999'), '不存在的条目 → None')

    # ── 到期回信路径 ────────────────────────────────────────────────────────

    def test_delivered_letter_enqueues_and_sends(self):
        self._enable_inbox()
        self._letter(with_turn=True)
        self._project()
        rows = self._deliveries()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].category, 'reply')
        result = self._dispatch(rows[0].id)
        self.assertEqual(result['state'], push.STATE_SENT)
        message = self.provider.sent[0][0]
        self.assertEqual(message['body'], push.GENERIC_TEXT[push.CATEGORY_DUE_LETTER])
        # 未交付（无回合）的回信不是候选
        self._letter(with_turn=False)
        self._project()
        self.assertEqual(len(self._deliveries()), 1, '未发布回信不入队')

    # ── W11 个人变化路径 ────────────────────────────────────────────────────

    def _care_item(self):
        """造一条已发布回顾发现条目（个人变化载体），返回条目 id。"""
        self._enable_inbox()
        window = '2026-11-23'
        self._review_turn(window)
        self._topic(kind='observation', category='walk_volume',
                    evidence=[{'window': window}], severity='info')
        self._project()
        with db.session_scope() as s:
            item = s.execute(select(AgentInboxItem)).scalars().one()
            return item.id

    def test_personal_change_enqueues_only_when_three_inputs_hold(self):
        """W11 三项齐备才入队；缺失相关性不得默认按 high。"""
        item_id = self._care_item()
        self.assertEqual(self._deliveries(), [], '证据未写入前不是候选')
        with db.session_scope() as s:
            inbox.mark_personal_eligibility(s, self.uid, item_id, relevance='normal',
                                            frozen_evidence=True, delivery_approved=True,
                                            now=self.now)
        self.assertEqual(self._deliveries(), [], '相关性非 high → 不入队')
        with self._gate_on():
            with db.session_scope() as s:
                inbox.mark_personal_eligibility(s, self.uid, item_id, relevance='high',
                                                frozen_evidence=True, delivery_approved=True,
                                                now=self.now)
        rows = self._deliveries()
        self.assertEqual(len(rows), 1, '三项齐备 → 首次合格入队')
        self.assertEqual(rows[0].category, 'care')
        # 重复写同值：不重复入队
        with self._gate_on():
            with db.session_scope() as s:
                inbox.mark_personal_eligibility(s, self.uid, item_id, relevance='high',
                                                frozen_evidence=True, delivery_approved=True,
                                                now=self.now)
        self.assertEqual(len(self._deliveries()), 1)

    def test_personal_change_quiet_mode_is_suppressed_at_send(self):
        """安静档：个人变化照常入队，发送前复核抑制且为终态；设备告警不受影响。"""
        item_id = self._care_item()
        with db.session_scope() as s:
            row = s.get(CompanionSettings, self.uid)
            row.proactivity = 0
            s.flush()
        with self._gate_on():
            with db.session_scope() as s:
                inbox.mark_personal_eligibility(s, self.uid, item_id, relevance='high',
                                                frozen_evidence=True, delivery_approved=True,
                                                now=self.now)
            row = s.get(AgentInboxItem, item_id)
            self.assertEqual(row.push_candidate, 1, '递交不改写 W12 的候选位')
        rows = self._deliveries()
        self.assertEqual(len(rows), 1, '安静档不拦入队，由发送前复核抑制')
        self.assertEqual(self._dispatch(rows[0].id)['state'], push.STATE_SUPPRESSED)
        self.assertEqual(self.provider.sent, [])
        self.assertEqual((self._deliveries()[0].state, self._deliveries()[0].reason),
                         (push.STATE_SUPPRESSED, 'quiet_mode'))
        # 调回标准档后再投影：不建新行，已抑制的也不复活（抑制是终态，不是延迟）
        with db.session_scope() as s:
            row = s.get(CompanionSettings, self.uid)
            row.proactivity = 1
            row.updated_at = self.now
            s.flush()
        self._project()
        rows = self._deliveries()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].state, rows[0].reason), (push.STATE_SUPPRESSED, 'quiet_mode'))

    # ── 重复递交：不改写候选位；闸门重开补上，晚开启不补推 ─────────────────

    def test_offer_never_rewrites_the_inbox_candidate_flag(self):
        """闸门关闭时不建行也不动 W12 的候选位；开闸后的下一轮投影补上。"""
        self._enable_inbox()
        topic_id = self._topic()
        self._topic_turn(topic_id)
        with db.session_scope() as s:            # 闸门关闭（环境默认）
            inbox.project_user(s, self.uid, now=self.now)
        self.assertEqual(self._deliveries(), [])
        self.assertEqual([item.push_candidate for item in self._items()], [1],
                         '候选位如实反映中心策略，不因闸门关闭被清掉')
        self._project()
        self.assertEqual(len(self._deliveries()), 1, '开闸后的下一轮投影入队')
        self._project()
        self.assertEqual(len(self._deliveries()), 1, '重复投影不重复入队')

    def test_item_created_as_candidate_enqueues_on_creation(self):
        """创建时就已是候选的条目（重启后恢复资格的那类）在创建事务里入队。"""
        self._enable_inbox()
        topic_id = self._topic()
        self._topic_turn(topic_id)
        with self._gate_on():
            with db.session_scope() as s:
                # W09/W11 的事务内投影入口：只插入条目，不经过富化
                pet = s.get(Pet, self.pet_id)
                created = inbox.project_source(s, pet, s.get(AgentCareTopic, topic_id))
        self.assertTrue(created)
        self.assertEqual(len(self._deliveries()), 1, '创建即候选 → 创建事务里入队')
        self._project()
        self.assertEqual(len(self._deliveries()), 1)

    def test_enabling_push_later_does_not_backlog_earlier_events(self):
        """事件先发生、用户之后才开启推送：不补推；开启之后发生的事件照常入队。"""
        with db.session_scope() as s:            # setUp 默认已开启：先关掉并解绑
            push.set_user_enabled(s, self.uid, False, now=self.now)
            push.unregister_installation(s, self.uid, INSTALLATION_A, now=self.now)
        self._enable_inbox()
        topic_id = self._topic()
        self._topic_turn(topic_id)
        self._project()
        self.assertEqual(self._deliveries(), [], '未开启推送时不建行')
        later = self.now + timedelta(hours=1)
        with db.session_scope() as s:
            push.set_user_enabled(s, self.uid, True, now=later)
            push.register_installation(s, self.uid, INSTALLATION_A, 'ios', TOKEN_A,
                                       permission_status='granted', now=later)
        self._project()
        self.assertEqual(self._deliveries(), [], '开启之前发生的事件被重新递交也不补推')
        # 开启之后发生的事件照常入队
        next_id = self._topic(anchor='anchor-2')
        self._topic_turn(next_id)
        with self._gate_on():
            with db.session_scope() as s:
                inbox.project_user(s, self.uid, now=later)
        rows = self._deliveries()
        newest = max(item.id for item in self._items())
        self.assertEqual([row.inbox_item_id for row in rows], [str(newest)],
                         '只有开启之后的那条入队')

    # ── app 装配 ────────────────────────────────────────────────────────────

    def test_create_app_installs_push_bridge(self):
        from petsense.app import create_app
        app = create_app(start_background=False)
        self.assertIsNotNone(push.SOURCE_RESOLVER, 'create_app 安装 W12 当前来源解析器')
        self.assertIsNotNone(app, '应用工厂可用')


if __name__ == '__main__':
    unittest.main()
