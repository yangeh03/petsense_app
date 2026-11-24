"""W13 原生推送确定性测试（agent-w13-spec.md §4 A1–A2 的自动验收）。

不接触真实服务方：Expo Push Service 由假 provider 驱动（本地假传输），覆盖消息
分类、安静档、资格复核、平台闸门、同一消息多设备、跨账号重新绑、退出、token
轮换、重启恢复与重复执行；以及票据成功、业务拒收、失效 token、明确可恢复拒收、
响应未知与「请求后中断」六类投递结果，核对所需重试与不重复发送。

真机与真实推送属于 A4/A5 验收（由负责人用 EAS 内部分发构建执行），不在本文件内。
"""
import hashlib
import json
import os
import secrets
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from helpers import fresh_db

from petsense import db
from petsense.agent import push, push_jobs, push_provider
from petsense.agent.push_routes import bp
from petsense.db import iso_z, utc_now
from petsense.db.models import AuthSession, Pet, User
from petsense.db.models.agent import CompanionSettings
from petsense.db.models.push import PushDelivery, PushInstallation
from sqlalchemy import select

INSTALLATION_A = 'install-aaaaaaaaaaaa'
INSTALLATION_B = 'install-bbbbbbbbbbbb'
TOKEN_A = 'ExponentPushToken[aaaa1111]'
TOKEN_B = 'ExponentPushToken[bbbb2222]'


class FakeProvider:
    """假 Expo 服务方：记录发送、返回预置票据与回执。"""

    def __init__(self):
        self.sent = []
        self.tickets = []
        self.receipts = {}
        self.unknown_on_send = False
        self.unknown_on_receipt = False
        self.receipt_error = None

    def send_messages(self, messages):
        self.sent.append(list(messages))
        if self.unknown_on_send:
            raise push_provider.OutcomeUnknown('投递请求结果未知')
        out = []
        for _ in messages:
            out.append(self.tickets.pop(0) if self.tickets else {'status': 'ok', 'id': 't-1'})
        return out

    def get_receipts(self, ticket_ids):
        if self.unknown_on_receipt:
            raise push_provider.OutcomeUnknown('回执请求结果未知')
        return {tid: self.receipts[tid] for tid in ticket_ids if tid in self.receipts}


def ticket_ok(ticket_id='t-1'):
    return {'status': 'ok', 'id': ticket_id, 'code': None, 'message': None}


def ticket_error(code, message='失败'):
    return {'status': 'error', 'id': None, 'code': code, 'message': message}


class FakeTransport:
    """假传输层：直接替换 Expo HTTP 客户端，票据与回执按预置返回。"""

    def __init__(self, tickets=None, receipts=None, raises=None):
        self.tickets = list(tickets or [{'status': 'ok', 'id': 't-1'}])
        self.receipts = receipts or {}
        self.raises = raises
        self.calls = []

    def post(self, url, payload):
        self.calls.append((url, payload))
        if self.raises is not None:
            raise self.raises
        if url == push_provider.SEND_URL:
            data = []
            for _ in payload.get('messages', []):
                data.append(self.tickets.pop(0) if self.tickets else {'status': 'ok', 'id': 't-1'})
            return {'data': data}
        ids = payload.get('ids', [])
        return {'data': {tid: self.receipts[tid] for tid in ids if tid in self.receipts}}


class W13PushTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.provider = FakeProvider()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.now = utc_now()
        # 假 W12 当前来源解析器：登记的状态 + 实时 petOwnerId（与真解析器同语义，
        # 宠物被转走时即使不改登记状态也立刻反映）
        self.sources = {}

        def fake_source(session, item_id):
            source = self.sources.get(item_id)
            if source is None:
                return None
            merged = dict(source)
            pet = session.get(Pet, merged.get('petId'))
            merged['petOwnerId'] = pet.user_id if pet is not None else None
            return merged

        push.set_inbox_source(fake_source)
        self.addCleanup(push.clear_inbox_source)
        with db.session_scope() as s:
            user = User(username='w13', display_name='w13', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(user)
            s.flush()
            self.uid = user.id
            other = User(username='w13b', display_name='w13b', password_hash='x', salt='s',
                         avatar_emoji='🙂', bio='', created_at=self.now)
            s.add(other)
            s.flush()
            self.other_uid = other.id
            s.add(CompanionSettings(user_id=user.id, assistant_name='照料助手', owner_title=None,
                                    verbosity='concise', proactivity=1, updated_at=self.now))
            s.add(CompanionSettings(user_id=other.id, assistant_name='照料助手', owner_title=None,
                                    verbosity='concise', proactivity=1, updated_at=self.now))
            pet = Pet(user_id=user.id, name='Leia', species='狗', breed='柴犬',
                      created_at=self.now)
            s.add(pet)
            s.flush()
            self.pet = pet.id
            token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=user.id, expires_at=self.now + timedelta(days=1)))
            self.auth = {'X-Petsense-Session': token}
            other_token = secrets.token_urlsafe(16)
            s.add(AuthSession(token_hash=hashlib.sha256(other_token.encode()).hexdigest(),
                              user_id=other.id, expires_at=self.now + timedelta(days=1)))
            self.other_auth = {'X-Petsense-Session': other_token}

    # ── 工具 ────────────────────────────────────────────────────────────────

    def _gate_on(self):
        return patch.dict(os.environ, {'PETSENSE_PUSH_IOS_ENABLED': '1'})

    def _gate_off(self):
        env = dict(os.environ)
        env.pop('PETSENSE_PUSH_IOS_ENABLED', None)
        env.pop('PETSENSE_PUSH_DISABLED', None)
        return patch.dict(os.environ, env, clear=True)

    def _register(self, installation_id=INSTALLATION_A, token=TOKEN_A, user_id=None,
                  permission='granted'):
        with db.session_scope() as s:
            row, _ = push.register_installation(s, user_id or self.uid, installation_id,
                                                'ios', token, permission_status=permission,
                                                now=self.now)
            return row.installation_id

    def _enable(self, user_id=None):
        with db.session_scope() as s:
            push.set_user_enabled(s, user_id or self.uid, True, now=self.now)

    def _proactivity(self, level, user_id=None):
        with db.session_scope() as s:
            row = s.get(CompanionSettings, user_id or self.uid)
            row.proactivity = level
            row.updated_at = self.now

    def _item(self, kind='device_fact', **kwargs):
        """W12 发布缝入参（push.ITEM_CONTRACT 形状）。是否可推由 W12 给出。"""
        item = {'id': 'inbox-1', 'userId': kwargs.pop('userId', self.uid), 'kind': kind,
                'petId': kwargs.pop('petId', self.pet),
                'pushCandidate': kwargs.pop('pushCandidate', True),
                'createdAt': kwargs.pop('createdAt', iso_z(self.now))}
        item.update(kwargs)
        return item

    def _source(self, item_id='inbox-1', **kwargs):
        """登记一条 W12「当前来源」状态（发送时按它复核；push.SOURCE_CONTRACT 形状）。"""
        source = {'userId': self.uid, 'petId': self.pet, 'petOwnerId': self.uid,
                  'sourceKind': 'device_fact', 'backfilled': False, 'published': True,
                  'severity': 'warning', 'sourceStatus': 'open', 'relevance': '',
                  'frozenEvidence': False, 'deliveryApproved': False, 'highRelevance': False,
                  'pushCandidate': True}
        source.update(kwargs)
        self.sources[item_id] = source
        return source

    def _publish(self, item=None, gate_on=True, source=None):
        """发布（W12 缝入口）。闸门关闭时不建行——默认按生产闸门开启的路径走。

        未显式给 source 时，按条目类型登记一条对应的「当前来源」状态。
        """
        item = item or self._item()
        if source is not None:
            self.sources[item['id']] = source
        elif item['id'] not in self.sources:
            self.sources[item['id']] = self._default_source(item)
        if gate_on:
            with self._gate_on():
                with db.session_scope() as s:
                    return push.publish_inbox_item(s, item, now=self.now)
        with self._gate_off():
            with db.session_scope() as s:
                return push.publish_inbox_item(s, item, now=self.now)

    def _default_source(self, item):
        kind = item['kind']
        source = {'userId': item['userId'], 'petId': self.pet, 'petOwnerId': item['userId'],
                  'sourceKind': kind, 'backfilled': False, 'published': True,
                  'sourceStatus': 'open', 'relevance': '', 'frozenEvidence': False,
                  'deliveryApproved': False, 'highRelevance': False, 'pushCandidate': True}
        if kind == 'device_fact':
            source['severity'] = item.get('severity') or 'warning'
        elif kind == 'reply':
            source['sourceStatus'] = 'delivered'
        elif kind == 'care':
            source.update({'severity': 'info', 'relevance': 'high', 'frozenEvidence': True,
                           'deliveryApproved': True, 'highRelevance': True})
        return source

    def _deliveries(self):
        with db.session_scope() as s:
            return s.execute(select(PushDelivery).order_by(PushDelivery.id)).scalars().all()

    def _delivery(self):
        rows = self._deliveries()
        return rows[0] if rows else None

    def _delivery_by_item(self, item_id):
        for row in self._deliveries():
            if row.inbox_item_id == item_id:
                return row
        return None

    def _installation(self, installation_id=INSTALLATION_A):
        with db.session_scope() as s:
            return s.execute(select(PushInstallation).where(
                PushInstallation.installation_id == installation_id)).scalar_one_or_none()

    # ── A1：消息分类 ────────────────────────────────────────────────────────

    def test_classify_follows_w12_push_candidate_flag(self):
        """资格判定只有一处（W12 中心策略）：W13 只按 pushCandidate 做分类映射。"""
        cases = [
            (self._item('device_fact'), push.CATEGORY_DEVICE_ALERT),
            (self._item('reply'), push.CATEGORY_DUE_LETTER),
            (self._item('care'), push.CATEGORY_PERSONAL_CHANGE),
        ]
        for item, expected in cases:
            self.assertEqual(push.classify_item(item)['category'], expected)
            self.assertEqual(push.classify_item(item)['inboxItemId'], 'inbox-1')
        # 中心策略判定不可推（info 设备事实、未发布的回信、证据不全的个人变化）
        self.assertIsNone(push.classify_item(self._item('device_fact', pushCandidate=False)))
        self.assertIsNone(push.classify_item(self._item('reply', pushCandidate=False)))
        self.assertIsNone(push.classify_item(self._item('care', pushCandidate=False)))
        # 学习完成只进收件箱，永不推送
        self.assertIsNone(push.classify_item(self._item('learning', pushCandidate=True)))
        # 历史补入不追溯推送
        self.assertIsNone(push.classify_item(self._item('device_fact', backfilled=True)))
        # 形状不合法（缺 id / 缺归属 / 未知来源类型）
        self.assertIsNone(push.classify_item({'userId': self.uid, 'kind': 'device_fact',
                                              'pushCandidate': True}))
        self.assertIsNone(push.classify_item({'id': 'inbox-1', 'kind': 'device_fact',
                                              'pushCandidate': True}))
        self.assertIsNone(push.classify_item(self._item('neighbor_news', pushCandidate=True)))
        self.assertIsNone(push.classify_item('not-a-dict'))

    def test_publish_requires_user_opt_in(self):
        self._register()
        result = self._publish()
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['reason'], 'user_disabled')
        self.assertEqual(self._deliveries(), [])

    def test_publish_creates_one_row_per_installation(self):
        self._enable()
        self._register(INSTALLATION_A, TOKEN_A)
        self._register(INSTALLATION_B, TOKEN_B)
        result = self._publish()
        self.assertEqual(result['published'], 2)
        self.assertEqual(len(self._deliveries()), 2)
        # 重复发布不重复建行（同一消息多设备去重）
        self.assertEqual(self._publish()['published'], 0)
        self.assertEqual(len(self._deliveries()), 2)

    def test_stale_event_outside_new_event_window_is_not_enqueued(self):
        """新事件窗口：超过 24h 才成为候选的历史条目不补推（只留站内）。"""
        self._enable()
        self._register()
        stale_time = self.now - timedelta(seconds=push.PUSH_EVENT_MAX_AGE_S + 60)
        result = self._publish(self._item(createdAt=iso_z(stale_time)))
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['reason'], 'stale_event')
        self.assertEqual(self._deliveries(), [])

    # ── A1：安静档 ──────────────────────────────────────────────────────────

    def test_quiet_mode_blocks_personal_change_at_publish(self):
        """安静档在发布时不建行（延迟而非作废）：不进入发送队列。"""
        self._enable()
        self._register()
        self._proactivity(0)
        result = self._publish(self._item('care'))
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['reason'], 'quiet_mode')
        self.assertEqual(self._deliveries(), [])

    def test_quiet_mode_set_after_publish_suppresses_at_send(self):
        """发布后才调到安静档：发送前复核按当前主动程度抑制。"""
        self._enable()
        self._register()
        self._publish(self._item('care'))
        delivery_id = self._delivery().id
        self._proactivity(0)
        with self._gate_on():
            push.dispatch_delivery(delivery_id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_SUPPRESSED)
        self.assertEqual(row.reason, 'quiet_mode')
        self.assertEqual(self.provider.sent, [])

    def test_device_alert_survives_quiet_mode(self):
        self._enable()
        self._register()
        self._proactivity(0)
        with self._gate_on():
            self._publish(self._item('device_fact'))
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SENT)

    def test_personal_change_recheck_follows_current_policy(self):
        """个人变化的证据门槛按 W12 **当前来源**复核（不信任发布时的缓存）。"""
        self._enable()
        self._register()
        self._proactivity(2)
        self._publish(self._item('care'))
        delivery_id = self._delivery().id
        # 当前来源的相关性被降级（缺高相关）→ 中心策略翻为不可推 → 抑制
        self.sources['inbox-1']['relevance'] = 'normal'
        self.sources['inbox-1']['highRelevance'] = False
        self.sources['inbox-1']['pushCandidate'] = False
        with self._gate_on():
            push.dispatch_delivery(delivery_id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'not_push_candidate')
        # 证据缺失同样按当前策略抑制
        with db.session_scope() as s:
            row = s.get(PushDelivery, delivery_id)
            row.state = push.STATE_PENDING
            row.reason = None
            s.flush()
        self.sources['inbox-1'].update({'relevance': 'high', 'highRelevance': True,
                                        'frozenEvidence': False, 'pushCandidate': False})
        with self._gate_on():
            push.dispatch_delivery(delivery_id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'not_push_candidate')
        self.assertEqual(self.provider.sent, [])

    # ── A1：关闭平台 ────────────────────────────────────────────────────────

    def test_platform_gate_closed_blocks_sending(self):
        self._enable()
        self._register()
        self._publish()
        with self._gate_off():
            self.assertFalse(push.platform_enabled('ios'))
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self.provider.sent, [])
        self.assertEqual(self._delivery().state, push.STATE_PENDING)  # 保留待发，不消耗重试
        # 闸门打开后仍可发送（快速停发不是销毁队列）
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SENT)

    def test_kill_switch_stops_all_platforms(self):
        with patch.dict(os.environ, {'PETSENSE_PUSH_IOS_ENABLED': '1',
                                      'PETSENSE_PUSH_DISABLED': '1'}):
            self.assertFalse(push.platform_enabled('ios'))
        self.assertFalse(push.platform_enabled('android'))
        self.assertFalse(push.platform_enabled('web'))

    def test_android_and_web_are_never_enabled(self):
        with self._gate_on():
            self.assertTrue(push.platform_enabled('ios'))
            self.assertFalse(push.platform_enabled('android'))
            self.assertFalse(push.platform_enabled('web'))

    # ── A1：资格复核 ────────────────────────────────────────────────────────

    def test_optout_after_publish_suppresses_delivery(self):
        self._enable()
        self._register()
        self._publish()
        with db.session_scope() as s:
            push.set_user_enabled(s, self.uid, False, now=self.now)
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_SUPPRESSED)
        self.assertEqual(row.reason, 'user_disabled')
        self.assertEqual(self.provider.sent, [])

    def test_permission_revoked_suppresses_delivery(self):
        self._enable()
        self._register()
        self._publish()
        with db.session_scope() as s:
            push.update_installation_permission(s, self.uid, INSTALLATION_A,
                                                push.PERMISSION_REVOKED, now=self.now)
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_SUPPRESSED)
        self.assertEqual(row.reason, 'permission_not_granted')

    # ── A1：跨账号重新绑 / 退出 / token 轮换 ───────────────────────────────

    def test_rebind_to_other_account_drops_old_ownership(self):
        self._enable()
        self._register()
        self._publish()
        # 同一安装换账号
        with db.session_scope() as s:
            push.register_installation(s, self.other_uid, INSTALLATION_A, 'ios', TOKEN_B,
                                       permission_status='granted', now=self.now)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_SUPPRESSED)
        self.assertEqual(row.reason, 'ownership_changed')
        installation = self._installation()
        self.assertEqual(installation.user_id, self.other_uid)
        self.assertEqual(installation.expo_token, TOKEN_B)

    def test_logout_revokes_installation_and_drops_pending(self):
        self._enable()
        self._register()
        self._publish()
        with db.session_scope() as s:
            push.unregister_installation(s, self.uid, INSTALLATION_A, now=self.now)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_SUPPRESSED)
        self.assertEqual(row.reason, 'logged_out')
        self.assertEqual(self._installation().state, push.INSTALLATION_REVOKED)

    def test_token_rotation_keeps_single_delivery(self):
        self._enable()
        self._register()
        self._publish()
        with db.session_scope() as s:
            push.update_installation_token(s, self.uid, INSTALLATION_A, TOKEN_B, now=self.now)
        self.assertEqual(len(self._deliveries()), 1)
        installation = self._installation()
        self.assertEqual(installation.expo_token, TOKEN_B)
        self.assertEqual(installation.previous_token_digest, push.token_digest(TOKEN_A))
        self.assertEqual(installation.token_version, 2)
        # 审计里只有摘要，没有 token 明文
        self.assertNotIn(TOKEN_A, str(installation.previous_token_digest))

    def test_repeated_execution_sends_once(self):
        self._enable()
        self._register()
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(len(self.provider.sent), 1)
        self.assertEqual(self._delivery().state, push.STATE_SENT)

    # ── A2：投递结果 ────────────────────────────────────────────────────────

    def test_ticket_success_then_delivered_receipt(self):
        self._enable()
        self._register()
        self.provider.tickets = [ticket_ok('ticket-abc')]
        self.provider.receipts = {'ticket-abc': {'status': 'delivered', 'id': 'ticket-abc',
                                                 'message': None, 'details': {}}}
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_SENT)
        self.assertEqual(row.expo_ticket_id, 'ticket-abc')
        with self._gate_on():
            push.sweep_receipts(now=self.now + timedelta(seconds=120), provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_DELIVERED)
        self.assertEqual(row.receipt_status, 'delivered')

    def test_ticket_business_rejection_fails_without_retry(self):
        self._enable()
        self._register()
        self.provider.tickets = [ticket_error(push_provider.ERROR_MESSAGE_TOO_BIG)]
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_FAILED)
        self.assertEqual(row.error_code, push_provider.ERROR_MESSAGE_TOO_BIG)
        self.assertEqual(row.attempts, 1)
        self.assertEqual(len(self.provider.sent), 1)

    def test_device_not_registered_rejects_and_unregisters(self):
        self._enable()
        self._register()
        self.provider.tickets = [ticket_error(push_provider.ERROR_DEVICE_NOT_REGISTERED)]
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_REJECTED)
        self.assertEqual(row.reason, 'device_not_registered')
        self.assertEqual(self._installation().state, push.INSTALLATION_UNREGISTERED)

    def test_recoverable_rejection_retries_then_exhausts(self):
        self._enable()
        self._register()
        self.provider.tickets = [ticket_error(push_provider.ERROR_MESSAGE_RATE_TOO_HIGH)] * 5
        self._publish()
        with self._gate_on():
            now = self.now
            push.dispatch_delivery(self._delivery().id, now=now, provider=self.provider)
            row = self._delivery()
            self.assertEqual(row.state, push.STATE_PENDING)
            self.assertEqual(row.reason, 'retry_scheduled')
            self.assertEqual(row.attempts, 1)
            for _ in range(3):
                now = now + timedelta(seconds=600)
                push.dispatch_delivery(self._delivery().id, now=now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_FAILED)
        self.assertEqual(row.reason, 'retry_exhausted')
        self.assertEqual(row.attempts, row.max_attempts)
        self.assertEqual(len(self.provider.sent), row.max_attempts)

    def test_unknown_outcome_stops_retry(self):
        self._enable()
        self._register()
        self.provider.unknown_on_send = True
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_UNKNOWN)
        self.assertEqual(row.reason, 'outcome_unknown')
        # 再跑一次不会补发（未知即停）
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(len(self.provider.sent), 1)
        self.assertEqual(self._delivery().state, push.STATE_UNKNOWN)

    def test_interrupted_after_dispatch_marked_unknown_on_recovery(self):
        """进程在外部调用后、落库前中断：行停在 sending，重启后按未知处理且不补发。"""
        self._enable()
        self._register()
        self._publish()
        with db.session_scope() as s:
            row = s.execute(select(PushDelivery)).scalars().one()
            row.state = push.STATE_SENDING
            row.attempts = 1
            row.updated_at = self.now - timedelta(seconds=600)
            s.flush()
            self.delivery_id = row.id
        recovered = push.recover_stuck(now=self.now)
        self.assertEqual(recovered, 1)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_UNKNOWN)
        self.assertEqual(row.reason, 'interrupted_after_dispatch')
        with self._gate_on():
            push.dispatch_delivery(self.delivery_id, now=self.now, provider=self.provider)
        self.assertEqual(self.provider.sent, [])

    def test_receipt_device_not_registered_unregisters_installation(self):
        self._enable()
        self._register()
        self.provider.tickets = [ticket_ok('t-x')]
        self.provider.receipts = {'t-x': {'status': 'error', 'id': 't-x', 'message': 'no',
                                           'details': {'error': push_provider.ERROR_DEVICE_NOT_REGISTERED}}}
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
            push.sweep_receipts(now=self.now + timedelta(seconds=120), provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_REJECTED)
        self.assertEqual(self._installation().state, push.INSTALLATION_UNREGISTERED)

    def test_failed_delivery_keeps_inbox_source_reachable(self):
        """投递失败不影响站内：条目 ID 仍在投递行里，失败后不重复建行。"""
        self._enable()
        self._register()
        self.provider.tickets = [ticket_error(push_provider.ERROR_MESSAGE_TOO_BIG)]
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_FAILED)
        self.assertEqual(row.inbox_item_id, 'inbox-1')
        self.assertEqual(self._publish()['published'], 0)
        self.assertEqual(len(self._deliveries()), 1)

    # ── 载荷与隐私 ──────────────────────────────────────────────────────────

    def test_payload_is_generic_and_carries_stable_inbox_id(self):
        self._enable()
        self._register()
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        message = self.provider.sent[0][0]
        self.assertEqual(message['data']['inboxItemId'], 'inbox-1')
        self.assertEqual(message['title'], push.PUSH_TITLE)
        self.assertNotIn('Leia', message['body'])
        self.assertNotIn('token', message['body'].lower())
        self.assertEqual(message['body'], push.GENERIC_TEXT[push.CATEGORY_DEVICE_ALERT])

    def test_installation_public_never_exposes_token(self):
        self._register()
        with db.session_scope() as s:
            row = s.execute(select(PushInstallation)).scalars().one()
            public = push.installation_public(row)
        self.assertNotIn('expo_token', public)
        self.assertTrue(public['hasToken'])
        self.assertNotIn(TOKEN_A, str(public))

    # ── Expo 服务方客户端 ──────────────────────────────────────────────────

    def test_provider_parses_tickets_and_receipts(self):
        transport = FakeTransport(tickets=[{'status': 'ok', 'id': 'ticket-1'}],
                                  receipts={'ticket-1': {'status': 'delivered', 'id': 'ticket-1',
                                                         'message': None, 'details': {}}})
        with patch.object(push_provider, '_transport', transport):
            tickets = push_provider.send_messages([{'to': TOKEN_A, 'body': 'x'}])
            self.assertEqual(tickets[0]['status'], 'ok')
            self.assertEqual(tickets[0]['id'], 'ticket-1')
            receipts = push_provider.get_receipts(['ticket-1', 'missing'])
            self.assertEqual(receipts['ticket-1']['status'], 'delivered')
            self.assertNotIn('missing', receipts)

    def test_provider_normalizes_error_ticket(self):
        transport = FakeTransport(tickets=[{'status': 'error', 'details': {
            'error': push_provider.ERROR_DEVICE_NOT_REGISTERED, 'message': 'gone'}}])
        with patch.object(push_provider, '_transport', transport):
            tickets = push_provider.send_messages([{'to': TOKEN_A}])
        self.assertEqual(tickets[0]['status'], 'error')
        self.assertEqual(tickets[0]['code'], push_provider.ERROR_DEVICE_NOT_REGISTERED)

    def test_provider_marks_network_failure_as_unknown(self):
        transport = FakeTransport(raises=push_provider.OutcomeUnknown('超时'))
        with patch.object(push_provider, '_transport', transport):
            with self.assertRaises(push_provider.OutcomeUnknown):
                push_provider.send_messages([{'to': TOKEN_A}])

    # ── worker：W08 作业跑一轮 ──────────────────────────────────────────────

    def test_worker_tick_dispatches_and_sweeps_receipts(self):
        """W08 作业一轮：派发待发件 → 查回执 → 落到已送达。"""
        self._enable()
        self._register()
        self._publish()
        transport = FakeTransport(tickets=[{'status': 'ok', 'id': 'tick-1'}],
                                  receipts={'tick-1': {'status': 'delivered', 'id': 'tick-1',
                                                       'message': None, 'details': {}}})
        with self._gate_on():
            with patch.object(push_provider, '_transport', transport):
                push_jobs.handle_dispatch({'kind': push_jobs.DISPATCH_KIND}, self.now)
                row = self._delivery()
                self.assertEqual(row.state, push.STATE_SENT)
                self.assertEqual(row.expo_ticket_id, 'tick-1')
                push_jobs.handle_dispatch(
                    {'kind': push_jobs.DISPATCH_KIND}, self.now + timedelta(seconds=120))
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_DELIVERED)
        self.assertEqual(row.receipt_status, 'delivered')

    def test_worker_tick_is_a_noop_when_gate_closed(self):
        self._enable()
        self._register()
        self._publish()
        transport = FakeTransport()
        with self._gate_off():
            with patch.object(push_provider, '_transport', transport):
                push_jobs.handle_dispatch({'kind': push_jobs.DISPATCH_KIND}, self.now)
        self.assertEqual(transport.calls, [], '闸门关闭时不调用服务方')
        self.assertEqual(self._delivery().state, push.STATE_PENDING)

    # ── D8 / 当前来源复核 ───────────────────────────────────────────────────

    def test_info_device_fact_never_notifies(self):
        """D8：info 设备事实只进收件箱，warning/critical 才可推。"""
        self._enable()
        self._register()
        # 发布时中心策略已按 info 判为不可推 → 根本不建投递行
        self.assertEqual(self._publish(self._item('device_fact', severity='info',
                                                  pushCandidate=False))['published'], 0)
        # 发布时可推，但当前来源降为 info（策略翻为不可推）→ 发送前抑制
        self._publish(self._item('device_fact'))
        self.sources['inbox-1'].update({'severity': 'info', 'pushCandidate': False})
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'not_push_candidate')
        self.assertEqual(self.provider.sent, [])

    def test_closed_alert_is_suppressed_at_send_time(self):
        self._enable()
        self._register()
        self._publish(self._item('device_fact'))
        self.sources['inbox-1'].update({'sourceStatus': 'closed', 'pushCandidate': False})
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'not_push_candidate')

    def test_missing_source_suppresses(self):
        """W12 来源读不到（未接入/已删除）→ 保守不发送。"""
        self._enable()
        self._register()
        self._publish(self._item('device_fact'))
        self.sources.pop('inbox-1')
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'source_unavailable')

    def test_source_owner_change_suppresses(self):
        self._enable()
        self._register()
        self._publish(self._item('device_fact'))
        self.sources['inbox-1']['userId'] = self.other_uid
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'ownership_changed')

    def test_replaced_item_kind_suppresses(self):
        self._enable()
        self._register()
        self._publish(self._item('device_fact'))
        self.sources['inbox-1']['sourceKind'] = 'learning'
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'source_kind_changed')

    def test_backfilled_source_suppresses(self):
        self._enable()
        self._register()
        self._publish(self._item('device_fact'))
        self.sources['inbox-1']['backfilled'] = True
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'backfilled')

    def test_transferred_pet_suppresses(self):
        self._enable()
        self._register()
        self._publish(self._item('device_fact'))
        with db.session_scope() as s:
            pet = s.get(Pet, self.pet)
            pet.user_id = self.other_uid
            s.flush()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'pet_scope_changed')

    def test_quiet_mode_allows_warning_but_not_personal_change(self):
        """D7：安静档仍放行 warning 设备告警与到期回信，只抑制个人变化。"""
        self._enable()
        self._register()
        self._proactivity(0)
        self._publish(self._item('device_fact'))
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        self.assertEqual(self._delivery().state, push.STATE_SENT)
        # 到期回信同样不受安静档影响（发布路径不检查主动程度）
        self._register(INSTALLATION_B, TOKEN_B)
        result = self._publish(self._item('reply', id='inbox-2'))
        self.assertEqual(result['published'], 2, '回信入队不受安静档影响')

        # 个人变化在安静档下发布 → 不建行
        result = self._publish(self._item('care', id='inbox-3'))
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['reason'], 'quiet_mode')

    def test_due_letter_requires_published_delivery(self):
        """未交付的回信中心策略判为不可推 → 不建投递行（已交付后转交推送）。"""
        self._enable()
        self._register()
        result = self._publish(self._item('reply', pushCandidate=False))
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['reason'], 'not_pushable')
        self.assertEqual(self._deliveries(), [])

    # ── token 版本与归属 ────────────────────────────────────────────────────

    def test_same_token_on_another_installation_revokes_the_stale_one(self):
        """同一 token 只能绑一个安装实例：旧实例停用，旧账号的待发件作废。"""
        self._enable()
        self._register(INSTALLATION_A, TOKEN_A)
        self._publish()
        # 同一台设备换了安装 ID（或账号）后又注册了同一个 token
        self._register('install-cccccccccccc', TOKEN_A)
        stale = self._installation(INSTALLATION_A)
        self.assertEqual(stale.state, push.INSTALLATION_UNREGISTERED)
        self.assertEqual(stale.expo_token, '')
        self.assertEqual(self._delivery().state, push.STATE_SUPPRESSED)
        self.assertEqual(self._delivery().reason, 'token_rebound')

    def test_device_not_registered_does_not_revoke_a_newer_token(self):
        """旧 token 的失效回执不得把同一安装实例上的新 token 一起废掉。"""
        self._enable()
        self._register(INSTALLATION_A, TOKEN_A)
        self._publish()
        # 先用旧 token 发出去（记录当时的摘要与版本）
        self.provider.tickets = [{'status': 'ok', 'id': 't-old'}]
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        # 之后客户端换成新 token
        with db.session_scope() as s:
            push.update_installation_token(s, self.uid, INSTALLATION_A, TOKEN_B, now=self.now)
        # 旧票据的回执这时才回来：判定失效
        self.provider.receipts = {'t-old': {'status': 'error', 'id': 't-old', 'message': 'gone',
                                            'details': {'error':
                                                        push_provider.ERROR_DEVICE_NOT_REGISTERED}}}
        with self._gate_on():
            push.sweep_receipts(now=self.now + timedelta(seconds=120), provider=self.provider)
        installation = self._installation()
        self.assertEqual(installation.state, push.INSTALLATION_ACTIVE,
                         '新 token 不该被旧 token 的失效回执废掉')
        self.assertEqual(installation.expo_token, TOKEN_B)

    def test_device_not_registered_revokes_the_current_token(self):
        self._enable()
        self._register(INSTALLATION_A, TOKEN_A)
        self._publish()
        self.provider.tickets = [{'status': 'ok', 'id': 't-now'}]
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
            self.provider.receipts = {'t-now': {'status': 'error', 'id': 't-now', 'message': 'gone',
                                                'details': {'error':
                                                            push_provider.ERROR_DEVICE_NOT_REGISTERED}}}
            push.sweep_receipts(now=self.now + timedelta(seconds=120), provider=self.provider)
        self.assertEqual(self._installation().state, push.INSTALLATION_UNREGISTERED)

    def test_audit_never_stores_raw_token(self):
        self._enable()
        self._register(INSTALLATION_A, TOKEN_A)
        self._publish()
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
        row = self._delivery()
        self.assertNotIn(TOKEN_A, str(row.token_digest))
        self.assertNotIn(TOKEN_A, str(row.last_error))
        self.assertNotIn(TOKEN_A, str(row.payload_digest))

    def test_provider_error_message_scrubs_token(self):
        """服务方把 token 带进错误描述时，落库前必须清洗。"""
        self.assertEqual(push_provider.scrub('bad ExponentPushToken[aaaa1111] here'),
                         'bad [token] here')

    def test_receipt_window_expires_to_unknown(self):
        """回执窗口 24h：超出后不再等待，按未知处理。"""
        self._enable()
        self._register()
        self._publish()
        self.provider.tickets = [{'status': 'ok', 'id': 't-1'}]
        with self._gate_on():
            push.dispatch_delivery(self._delivery().id, now=self.now, provider=self.provider)
            push.sweep_receipts(now=self.now + timedelta(seconds=60), provider=self.provider)
            push.sweep_receipts(now=self.now + timedelta(seconds=25 * 3600),
                                provider=self.provider)
        row = self._delivery()
        self.assertEqual(row.state, push.STATE_UNKNOWN)
        self.assertEqual(row.reason, 'receipt_window_expired')

    def test_provider_rejects_oversized_payload(self):
        transport = FakeTransport()
        with patch.object(push_provider, '_transport', transport):
            with self.assertRaises(push_provider.ProviderError) as ctx:
                push_provider.send_messages([{'to': TOKEN_A, 'body': 'x' * 5000}])
        self.assertEqual(ctx.exception.code, 'payload_too_large')

    # ── HTTP 接口 ──────────────────────────────────────────────────────────

    def test_routes_reject_android_registration(self):
        response = self.client.post('/api/app/push/installations', headers=self.auth, json={
            'installationId': INSTALLATION_A, 'platform': 'android', 'expoToken': TOKEN_A,
            'permissionStatus': 'granted'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('仅支持 iOS', response.get_json()['message'])

    def test_routes_require_login(self):
        response = self.client.get('/api/app/push/status')
        self.assertEqual(response.status_code, 401)

    def test_route_register_and_preferences_roundtrip(self):
        response = self.client.post('/api/app/push/installations', headers=self.auth, json={
            'installationId': INSTALLATION_A, 'platform': 'ios', 'expoToken': TOKEN_A,
            'permissionStatus': 'granted'})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['action'], 'created')
        self.assertNotIn(TOKEN_A, str(body))
        self.assertTrue(self.client.put('/api/app/push/preferences', headers=self.auth,
                                        json={'enabled': True}).get_json()['enabled'])
        status = self.client.get('/api/app/push/status', headers=self.auth).get_json()
        self.assertTrue(status['enabled'])
        self.assertEqual(status['gates']['ios'], push.platform_enabled('ios'))
        self.assertEqual(len(status['installations']), 1)

    def test_route_token_rotation_keeps_single_installation(self):
        self.client.post('/api/app/push/installations', headers=self.auth, json={
            'installationId': INSTALLATION_A, 'platform': 'ios', 'expoToken': TOKEN_A,
            'permissionStatus': 'granted'})
        response = self.client.post(f'/api/app/push/installations/{INSTALLATION_A}/token',
                                    headers=self.auth,
                                    json={'expoToken': TOKEN_B, 'permissionStatus': 'granted'})
        self.assertEqual(response.status_code, 200)
        status = self.client.get('/api/app/push/status', headers=self.auth).get_json()
        self.assertEqual(len(status['installations']), 1)
        self.assertTrue(status['installations'][0]['tokenRotated'])

    def test_route_unregister_revokes_installation(self):
        self.client.post('/api/app/push/installations', headers=self.auth, json={
            'installationId': INSTALLATION_A, 'platform': 'ios', 'expoToken': TOKEN_A,
            'permissionStatus': 'granted'})
        response = self.client.delete(f'/api/app/push/installations/{INSTALLATION_A}',
                                      headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._installation().state, push.INSTALLATION_REVOKED)

    def test_other_account_cannot_touch_installation(self):
        self.client.post('/api/app/push/installations', headers=self.auth, json={
            'installationId': INSTALLATION_A, 'platform': 'ios', 'expoToken': TOKEN_A,
            'permissionStatus': 'granted'})
        response = self.client.delete(f'/api/app/push/installations/{INSTALLATION_A}',
                                      headers=self.other_auth)
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
