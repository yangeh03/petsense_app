"""W13 原生推送领域（首版仅 iOS）：发布缝、资格复核、投递状态机与审计。

与 W12 的关系是**单向**：W12 每轮投影与富化后都把当前仍是推送候选的条目交给
``publish_inbox_item(session, facts)``，并安装「当前来源解析器」（``set_inbox_source``）。
本模块**不 import** W12 的 ``inbox``，也不读写 W12 的表；只 import W12 的中心策略
模块 ``inbox_eligibility``（该模块不依赖 ``petsense``，不会形成环）。

**资格判定只有一处**：是否可推由 W12 的中心策略给出（``facts['pushCandidate']``），
本模块不重新推导严重度、状态或证据门槛，避免口径分叉。本模块只补 W13 自己的
几件事：平台闸门、用户开关、系统权限、安装实例与 token 有效性、归属、去重，
以及个人变化的安静档。

推送语义（D2/D4/D5/D7/D8）：

- **可推三类**：仍开放的关键设备告警（warning/critical）、明确委托的到期回信
  （due_letter）、高相关个人变化（personal_change）。info 设备事实、学习完成与
  迁移补入只留站内（D8）。
- **发送前重新核对 W12 当前来源**：不信任发布时的缓存 meta。发送前通过注入的
  ``inbox_source`` 解析器读回该条目的**当前**来源、拥有者、范围、是否补入以及
  设备告警的当前状态；解析器未接入时保守不发送。
- **个人变化四门槛**：W11 冻结证据引用存在 + 模型判定高相关被接受 + W09 已发布
  对象仍开放 + 当前主动程度 > 0。
- **安静档**：只抑制个人变化；warning/critical 设备告警与明确委托的到期回信仍
  遵守用户的开关与系统权限（D7）。
- **去重键**：``(收件箱条目, 安装实例)``。同一账号的多台设备各一行；token 轮换
  不换安装实例，因此轮换不会产生重复投递。
- **重复递交、恰好一次**：W12 每轮都重新递交当前候选，本模块按去重键建行。闸门
  关闭时不建行，开闸后由下一轮递交补上，且只补新事件窗口（24h）内的事件。
- **不补推旧事件**：事件必须晚于用户开启推送的时刻，也晚于这台设备具备接收条件
  的时刻。刚开启推送、重新登录或重新授权的设备不会收到之前的通知，那些内容留在
  收件箱里。
- **安静档是抑制而不是延迟**：个人变化入队后按发送时的主动程度判定，安静档下
  抑制即终态，调回标准档后不会补发。
- **未知即停**：调用服务方后若结果不明（超时、进程中断），标记 ``unknown`` 并
  停止该条重试——宁可少一次提醒，也不给用户重复打扰（收件箱不受影响）。

隐私约定：**token 明文只存在于安装实例的当前值**。审计行、错误信息与日志一律用
``token_digest``（SHA-256）与 ``token_version`` 比较，不落明文。
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from petsense.agent import inbox_eligibility
from petsense.agent import push_provider
from petsense.agent import settings as settings_service
from petsense.db import dialect_insert, iso_z, session_scope, utc_now
from petsense.db.models.push import PushDelivery, PushInstallation, PushPreference

logger = logging.getLogger(__name__)

# ── 平台与分类 ──────────────────────────────────────────────────────────────
PLATFORM_IOS = 'ios'
SUPPORTED_PLATFORMS = (PLATFORM_IOS,)   # 本轮只交付 iOS；Android 与 Web 保持站内
#: 推送分类**直接沿用 W12 的来源类型**（inbox_eligibility 的唯一口径）。
#: 不维护第二套映射：W12 的来源类型即 W13 的分类，载荷不能自选分类或归属。
CATEGORY_DEVICE_ALERT = inbox_eligibility.SOURCE_DEVICE_FACT   # device_fact
CATEGORY_DUE_LETTER = inbox_eligibility.SOURCE_REPLY            # reply
CATEGORY_PERSONAL_CHANGE = inbox_eligibility.SOURCE_CARE        # care
KIND_LEARNING = inbox_eligibility.SOURCE_LEARNING
SOURCE_KINDS = inbox_eligibility.SOURCE_KINDS

#: W13 只推这三类来源。学习完成（learning）只进收件箱，永不推送。
PUSHABLE_KINDS = (CATEGORY_DEVICE_ALERT, CATEGORY_DUE_LETTER, CATEGORY_PERSONAL_CHANGE)

#: 设备事实严重度：与 W12 共用口径（D8）。只有 warning/critical 可推。
SEVERITY_PUSHABLE = inbox_eligibility.KEY_DEVICE_SEVERITIES
SEVERITY_INFO = 'info'

# 安装实例状态
INSTALLATION_ACTIVE = 'active'
INSTALLATION_REVOKED = 'revoked'
INSTALLATION_UNREGISTERED = 'unregistered'

# 权限状态（客户端上报，服务端据此复核）
PERMISSION_GRANTED = 'granted'
PERMISSION_DENIED = 'denied'
PERMISSION_REVOKED = 'revoked'
PERMISSION_UNDETERMINED = 'undetermined'
PERMISSION_UNAVAILABLE = 'unavailable'
PERMISSION_VALUES = (PERMISSION_GRANTED, PERMISSION_DENIED, PERMISSION_REVOKED,
                     PERMISSION_UNDETERMINED, PERMISSION_UNAVAILABLE)

# 投递状态
STATE_PENDING = 'pending'
STATE_SENDING = 'sending'
STATE_SENT = 'sent'
STATE_DELIVERED = 'delivered'
STATE_FAILED = 'failed'
STATE_REJECTED = 'rejected'
STATE_UNKNOWN = 'unknown'
STATE_SUPPRESSED = 'suppressed'
TERMINAL_STATES = (STATE_DELIVERED, STATE_FAILED, STATE_REJECTED, STATE_UNKNOWN, STATE_SUPPRESSED)

# 通用文案（D3：锁屏只显示通用提示，不含宠物名、数值或任何私密内容）
GENERIC_TEXT = {
    CATEGORY_DEVICE_ALERT: '有一项设备提醒待处理',
    CATEGORY_DUE_LETTER: '有一份委托回信待查看',
    CATEGORY_PERSONAL_CHANGE: '有一条值得留意的新变化',
}

PUSH_TITLE = 'PetSense'

MAX_ATTEMPTS = int(os.environ.get('PETSENSE_PUSH_MAX_ATTEMPTS', '3'))
RETRY_BACKOFF_BASE_S = 60
RECEIPT_WAIT_S = 60            # 投递后多久开始查回执
RECEIPT_POLL_INTERVAL_S = 60   # 回执未就绪时的下次轮询间隔
RECEIPT_BATCH = 100            # 单次回执查询的票据上限
RECEIPT_WINDOW_S = 24 * 3600   # 回执窗口：超过 24h 未拿到回执即按「未知」处理
PENDING_MAX_AGE_S = 24 * 3600  # pending 超过此龄期不再补发（避免开闸后刷屏）
#: 新事件窗口：只推**近 24 小时内创建**的收件箱条目。更早的事件（历史补入、
#: 迟到恢复的旧事件）永远只在收件箱里，不产生系统通知。
PUSH_EVENT_MAX_AGE_S = 24 * 3600
STUCK_SENDING_S = 300          # sending 停留超过此时长即判定为「调用后中断」

#: W12 → W13 的发布缝入参契约（docs/agent-w13-integration-seam.md §1）。
#: W12 给出**当前**事实；是否可推由 W12 的中心策略决定（``pushCandidate``），
#: W13 不重新推导严重度、状态或证据门槛。
ITEM_CONTRACT = {
    'id': 'string，必填。W12 的稳定收件箱 ID（深链唯一键）。',
    'userId': 'int，必填。条目拥有者。',
    'kind': 'string，必填。W12 来源类型：device_fact / reply / care / learning。',
    'petId': 'int，必选。宠物归属，用于归属复核与审计。',
    'pushCandidate': 'bool，必填。W12 中心策略给出的推送候选判定（唯一口径）。',
    'severity': "string，可选。来源当前严重度：info / warning / critical。",
    'sourceStatus': "string，可选。来源当前状态（open / ongoing / closed / delivered）。",
    'relevance': "string，可选。'high' 表示模型判定高相关。",
    'frozenEvidence': 'bool，可选。W11 已冻结证据（个人变化必需）。',
    'deliveryApproved': 'bool，可选。W09 已批准交付（个人变化必需）。',
    'highRelevance': 'bool，可选。W11 写入的高相关标记。',
    'backfilled': 'bool，可选。历史补入条目：永不进入发送队列。',
    'createdAt': "string，可选。条目创建时间（ISO-Z）；用于「新事件窗口」判定。",
}

#: W12 当前来源解析器：fn(session, inbox_item_id) -> dict | None。
#: 由 W12 安装（``inbox.install_push_bridge()``）。未安装时发送一律保守抑制——
#: 绝不凭缓存信息给用户发通知。返回的 dict 形状见 ``SOURCE_CONTRACT``。
SOURCE_CONTRACT = {
    'userId': 'int，当前条目拥有者（已转让的宠物会与拥有者不一致）。',
    'petId': 'int，条目所属宠物。',
    'petOwnerId': 'int，宠物**当前**拥有者；宠物被转走或删除时与 userId 不同/为空。',
    'sourceKind': 'string，来源类型（device_fact / reply / care / learning）。',
    'backfilled': 'bool，历史补入。',
    'published': 'bool，来源当前是否已发布（按实时回合身份判定）。',
    'severity': 'string，来源当前严重度。',
    'sourceStatus': 'string，来源当前状态。',
    'relevance': 'string，W11 明确相关性。',
    'frozenEvidence': 'bool，W11 已冻结证据。',
    'deliveryApproved': 'bool，W09 已批准交付。',
    'highRelevance': 'bool，W11 高相关标记。',
    'pushCandidate': 'bool，中心策略的**当前**推送候选判定（唯一口径）。',
}
SOURCE_RESOLVER = None


def set_inbox_source(resolver):
    """安装 W12 当前来源解析器（合入时调用一次即可）。"""
    global SOURCE_RESOLVER
    SOURCE_RESOLVER = resolver


def clear_inbox_source():
    """移除解析器（测试隔离用）。"""
    global SOURCE_RESOLVER
    SOURCE_RESOLVER = None


def current_source(session, inbox_item_id):
    """读回 W12 的当前来源状态。解析器未接入或读取失败时返回 None。"""
    if SOURCE_RESOLVER is None:
        return None
    try:
        source = SOURCE_RESOLVER(session, inbox_item_id)
    except Exception:  # noqa: BLE001 解析失败按「来源不可用」处理，不影响 worker
        logger.exception('push source resolver failed for item')
        return None
    return source if isinstance(source, dict) else None


def _env_flag(name):
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'on', 'yes')


def platform_enabled(platform):
    """平台闸门：默认全关。

    - ``PETSENSE_PUSH_DISABLED=1``：全局急停，任何平台都不发。
    - ``PETSENSE_PUSH_IOS_ENABLED=1``：放开 iOS。默认关闭。
    - 非 iOS 平台本轮一律不发（Android 与 Web 保持站内收件箱）。
    """
    if _env_flag('PETSENSE_PUSH_DISABLED'):
        return False
    if platform not in SUPPORTED_PLATFORMS:
        return False
    return _env_flag('PETSENSE_PUSH_IOS_ENABLED')


def platform_gate_status():
    """给 App 与运维看的闸门快照（不含任何凭据）。"""
    return {
        'ios': platform_enabled(PLATFORM_IOS),
        'android': False,
        'web': False,
        'supportedPlatforms': list(SUPPORTED_PLATFORMS),
        'killSwitch': _env_flag('PETSENSE_PUSH_DISABLED'),
    }


# ── token 摘要 ──────────────────────────────────────────────────────────────

def token_digest(expo_token):
    """token 摘要（SHA-256 十六进制）。比较与审计只用摘要，不落明文。"""
    if not expo_token:
        return ''
    return hashlib.sha256(expo_token.encode('utf-8')).hexdigest()


# ── W12 发布缝：分类与规范化 ────────────────────────────────────────────────

def classify_item(facts):
    """把 W12 的**当前**事实规范化成推送候选；不可推返回 None。

    资格判定**不在这里**：是否可推由 W12 中心策略给出（``facts['pushCandidate']``，
    已含 D8 严重度口径、当前开放状态、W11 证据门槛与历史补入排除）。本函数只做
    形状校验与分类映射，不重复推导策略。
    """
    if not isinstance(facts, dict):
        return None
    item_id = str(facts.get('id') or '').strip()
    if not item_id:
        return None
    user_id = facts.get('userId')
    if user_id is None:
        return None
    kind = str(facts.get('kind') or '').strip()
    if kind not in SOURCE_KINDS:
        return None
    if kind not in PUSHABLE_KINDS:
        return None          # 学习完成只进收件箱
    if facts.get('backfilled'):
        return None          # 历史补入不追溯推送
    if not facts.get('pushCandidate'):
        return None          # 中心策略判定不是推送候选
    severity = str(facts.get('severity') or '').strip().lower() or None
    relevance = str(facts.get('relevance') or '').strip().lower() or None
    return {
        'inboxItemId': item_id,
        'userId': int(user_id),
        'petId': facts.get('petId'),
        'category': kind,
        'severity': severity,
        'relevance': relevance,
        'sourceStatus': str(facts.get('sourceStatus') or '').strip() or None,
        'frozenEvidence': bool(facts.get('frozenEvidence')),
        'deliveryApproved': bool(facts.get('deliveryApproved')),
        'highRelevance': bool(facts.get('highRelevance')),
        'createdAt': facts.get('createdAt'),
    }


def _parse_iso(value):
    """解析 ISO-Z 时间；解析不了返回 None（不做年龄判定，交给调用方兜底）。"""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_created(facts):
    """条目创建时间（aware UTC）；无法解析时返回 None（表示「未知龄期」）。"""
    return _parse_iso(facts.get('createdAt'))


def _precedes(moment, threshold):
    """``moment`` 是否严格早于 ``threshold``。

    条目创建时间经 ``iso_z`` 传出时只保留毫秒，而库里的时间戳是微秒：两边先统一到
    毫秒再比，同一时刻写下的时间不会被截断误差判成「更早」。
    """
    def milliseconds(value):
        return value.replace(microsecond=(value.microsecond // 1000) * 1000)
    return milliseconds(moment) < milliseconds(threshold)


def dedup_key(inbox_item_id, installation_id):
    """投递去重键：收件箱条目 + 安装实例（D5）。"""
    return '%s:%s' % (inbox_item_id, installation_id)


def _digest(payload):
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def build_message(expo_token, category, inbox_item_id):
    """构造 Expo 载荷：通用文案 + 稳定收件箱 ID + 必要路由元数据。

    载荷不含宠物名、数值、证据内容等私密信息（锁屏可见部分必须是通用提示）。
    """
    return {
        'to': expo_token,
        'title': PUSH_TITLE,
        'body': GENERIC_TEXT.get(category, GENERIC_TEXT[CATEGORY_DEVICE_ALERT]),
        'data': {
            'inboxItemId': inbox_item_id,
            'category': category,
        },
        'sound': 'default',
        'priority': 'high',
    }


# ── 用户开关与安装实例 ───────────────────────────────────────────────────────

def user_enabled(session, user_id):
    """用户级推送开关（默认关闭）。"""
    row = session.get(PushPreference, user_id)
    return bool(row and row.enabled)


def set_user_enabled(session, user_id, enabled, now=None):
    """写用户开关。关闭时把该用户全部待发件抑制（不从队列复活）。"""
    now = now or utc_now()
    row = session.get(PushPreference, user_id)
    if row is None:
        row = PushPreference(user_id=user_id, enabled=1 if enabled else 0, updated_at=now)
        session.add(row)
    else:
        row.enabled = 1 if enabled else 0
        row.updated_at = now
    if not enabled:
        suppress_pending(session, user_id, 'user_disabled', now=now)
    return row


def suppress_pending(session, user_id, reason, now=None, installation_id=None):
    """把该用户（可选限定安装实例）的待发件置为 suppressed。返回抑制条数。"""
    now = now or utc_now()
    stmt = update(PushDelivery).where(
        PushDelivery.user_id == user_id,
        PushDelivery.state == STATE_PENDING,
    )
    if installation_id is not None:
        stmt = stmt.where(PushDelivery.installation_id == installation_id)
    result = session.execute(stmt.values(state=STATE_SUPPRESSED, reason=reason,
                                          updated_at=now, finished_at=now))
    return result.rowcount or 0


def active_installations(session, user_id, platform=PLATFORM_IOS):
    """该用户在该平台上处于可发送状态的安装实例。"""
    return session.execute(select(PushInstallation).where(
        PushInstallation.user_id == user_id,
        PushInstallation.platform == platform,
        PushInstallation.state == INSTALLATION_ACTIVE,
    )).scalars().all()


def _installation_eligible(row):
    """这台设备此刻能否接收：在用、有 token、系统已授权。"""
    return (row.state == INSTALLATION_ACTIVE and bool(row.expo_token)
            and row.permission_status == PERMISSION_GRANTED)


def _refresh_eligible_since(row, now):
    """维护「具备接收条件的起始时间」：刚具备时记下，中断即清空（恢复时重新计时）。

    发布只给这个时刻之后发生的事件建行，所以开启推送、重新登录或重新授权都不会
    把之前的通知补发到这台设备上。
    """
    if _installation_eligible(row):
        if row.eligible_since is None:
            row.eligible_since = now
    else:
        row.eligible_since = None


def _revoke_same_token(session, expo_token, keep_installation_id, now):
    """同一 Expo token 只允许绑定一个安装实例。

    同 token 再次注册（换账号、换安装 ID、重装后换了 install id）时，旧安装实例
    立即停用并清空 token——旧账号的待发件一并作废，新主人不会收到旧账号的消息，
    同一台设备也不会收到两份。
    """
    stale = session.execute(select(PushInstallation).where(
        PushInstallation.expo_token == expo_token,
        PushInstallation.installation_id != keep_installation_id)).scalars().all()
    for row in stale:
        row.state = INSTALLATION_UNREGISTERED
        row.expo_token = ''            # 清空以释放唯一约束；摘要留在库里做审计
        row.eligible_since = None
        row.updated_at = now
        suppress_pending(session, row.user_id, 'token_rebound', now=now,
                         installation_id=row.installation_id)
    return len(stale)


def register_installation(session, user_id, installation_id, platform, expo_token,
                          permission_status=PERMISSION_GRANTED, now=None):
    """绑定安装实例（登录后注册）。

    - 同一安装换账号：归属整体迁移，旧归属的待发件立即从发送集合剔除。
    - 同一安装重复注册 / token 轮换：只换 token 与权限状态，安装实例 ID 不变，
      因此去重键不变，不会产生重复投递；token 版本号 +1。
    - 同一 token 出现在另一个安装实例上：旧实例立即停用（见 ``_revoke_same_token``）。
    """
    now = now or utc_now()
    digest = token_digest(expo_token)
    _revoke_same_token(session, expo_token, installation_id, now)
    row = session.execute(select(PushInstallation).where(
        PushInstallation.installation_id == installation_id)).scalar_one_or_none()
    if row is None:
        row = PushInstallation(installation_id=installation_id, user_id=user_id,
                               platform=platform, expo_token=expo_token,
                               token_digest=digest, token_version=1,
                               permission_status=permission_status,
                               state=INSTALLATION_ACTIVE, created_at=now, updated_at=now)
        session.add(row)
        _refresh_eligible_since(row, now)
        return row, 'created'
    if row.user_id != user_id:
        # 换账号：旧归属的待发件作废，旧 token 立即失效；新主人从这一刻起才算具备接收条件
        suppress_pending(session, row.user_id, 'ownership_changed', now=now,
                         installation_id=installation_id)
        row.user_id = user_id
        row.state = INSTALLATION_ACTIVE
        row.eligible_since = None
    if row.expo_token != expo_token:
        row.previous_token_digest = row.token_digest or None
        row.expo_token = expo_token
        row.token_digest = digest
        row.token_version = (row.token_version or 1) + 1
    row.permission_status = permission_status
    # 本人重新注册即重新启用（退出登录留下的 revoked、服务方判过失效的 unregistered）
    row.state = INSTALLATION_ACTIVE
    row.updated_at = now
    row.last_seen_at = now
    _refresh_eligible_since(row, now)
    return row, 'updated'


def update_installation_token(session, user_id, installation_id, expo_token,
                              permission_status=None, now=None):
    """token 轮换（仅本人安装实例）。版本号 +1，旧摘要留档。"""
    now = now or utc_now()
    row = session.execute(select(PushInstallation).where(
        PushInstallation.installation_id == installation_id,
        PushInstallation.user_id == user_id)).scalar_one_or_none()
    if row is None:
        return None
    if expo_token and row.expo_token != expo_token:
        _revoke_same_token(session, expo_token, installation_id, now)
        row.previous_token_digest = row.token_digest or None
        row.expo_token = expo_token
        row.token_digest = token_digest(expo_token)
        row.token_version = (row.token_version or 1) + 1
    if permission_status:
        row.permission_status = permission_status
        if permission_status != PERMISSION_GRANTED:
            suppress_pending(session, user_id, 'permission_not_granted', now=now,
                             installation_id=installation_id)
    row.updated_at = now
    row.last_seen_at = now
    _refresh_eligible_since(row, now)
    return row


def update_installation_permission(session, user_id, installation_id, permission_status, now=None):
    """权限变化（系统撤回 / 系统拒绝）。撤回后立即停止发送。"""
    now = now or utc_now()
    row = session.execute(select(PushInstallation).where(
        PushInstallation.installation_id == installation_id,
        PushInstallation.user_id == user_id)).scalar_one_or_none()
    if row is None:
        return None
    row.permission_status = permission_status
    row.updated_at = now
    row.last_seen_at = now
    if permission_status != PERMISSION_GRANTED:
        suppress_pending(session, user_id, 'permission_not_granted', now=now,
                         installation_id=installation_id)
    _refresh_eligible_since(row, now)
    return row


def unregister_installation(session, user_id, installation_id, now=None):
    """退出登录 / 解除绑定：安装实例停用，待发件作废。

    必须在**会话仍有效**时调用（客户端须先解绑再登出），否则服务端无法认证这次
    请求，也就无法把旧归属从发送集合剔除。
    """
    now = now or utc_now()
    row = session.execute(select(PushInstallation).where(
        PushInstallation.installation_id == installation_id,
        PushInstallation.user_id == user_id)).scalar_one_or_none()
    if row is None:
        return None
    row.state = INSTALLATION_REVOKED
    row.updated_at = now
    _refresh_eligible_since(row, now)
    suppress_pending(session, user_id, 'logged_out', now=now, installation_id=installation_id)
    return row


def installation_public(row):
    """对外序列化：**不含 token，也不含 token 摘要**。"""
    if row is None:
        return None
    return {
        'installationId': row.installation_id,
        'platform': row.platform,
        'state': row.state,
        'permissionStatus': row.permission_status,
        'hasToken': bool(row.expo_token),
        'tokenRotated': bool(row.previous_token_digest),
        'tokenVersion': row.token_version,
        'eligible': _installation_eligible(row),
        'lastSeenAt': iso_z(row.last_seen_at) if row.last_seen_at else None,
        'updatedAt': iso_z(row.updated_at),
    }


def delivery_public(row):
    """投递审计行对外序列化（不含 token、摘要与载荷正文）。"""
    if row is None:
        return None
    return {
        'id': row.id,
        'inboxItemId': row.inbox_item_id,
        'installationId': row.installation_id,
        'category': row.category,
        'state': row.state,
        'reason': row.reason,
        'errorCode': row.error_code,
        'attempts': row.attempts,
        'ticketStatus': row.ticket_status,
        'receiptStatus': row.receipt_status,
        'createdAt': iso_z(row.created_at),
        'sentAt': iso_z(row.sent_at) if row.sent_at else None,
        'finishedAt': iso_z(row.finished_at) if row.finished_at else None,
    }


# ── 发布：为每个可发送安装实例建投递行 ──────────────────────────────────────

def publish_inbox_item(session, facts, now=None):
    """W12 发布缝入口：把一条当前仍是推送候选的收件条目入队（重复调用是幂等的）。

    入参是 W12 给出的**当前**事实（形状见 ``ITEM_CONTRACT``）。返回
    ``{'published': n, 'reason': str|None, 'category': str|None}``；不可推、闸门关闭、
    超出新事件窗口、用户未开启、事件早于开启时刻或没有可接收的设备时 published=0。

    幂等：去重键为 ``(收件箱条目, 安装实例)``，W12 每轮重新递交同一条目只会有一行。
    闸门关闭时不建行，之后每轮递交都会再来一次，所以开闸后自然补上。
    """
    now = now or utc_now()
    candidate = classify_item(facts)
    if candidate is None:
        return {'published': 0, 'reason': 'not_pushable', 'category': None}
    category = candidate['category']
    if not platform_enabled(PLATFORM_IOS):
        # 闸门关闭时不建行；开闸后也只补「新事件窗口」内的事件，不补历史。
        return {'published': 0, 'reason': 'platform_disabled', 'category': category}
    created = _event_created(candidate)
    if created is not None and (now - created).total_seconds() > PUSH_EVENT_MAX_AGE_S:
        # 历史事件不补推（迟到恢复的旧事件也算历史）。
        return {'published': 0, 'reason': 'stale_event', 'category': category}
    user_id = candidate['userId']
    preference = session.get(PushPreference, user_id)
    if preference is None or not preference.enabled:
        return {'published': 0, 'reason': 'user_disabled', 'category': category}
    if created is not None and preference.updated_at and _precedes(created, preference.updated_at):
        # 开启推送之前发生的事件不补推（留在站内收件箱）。
        return {'published': 0, 'reason': 'before_opt_in', 'category': category}
    published = 0
    for installation in active_installations(session, user_id, PLATFORM_IOS):
        if not _installation_eligible(installation):
            continue
        if created is not None and (installation.eligible_since is None
                                    or _precedes(created, installation.eligible_since)):
            continue  # 这台设备具备接收条件之前发生的事件不补推
        meta = {
            'severity': candidate.get('severity'),
            'sourceStatus': candidate.get('sourceStatus'),
            'relevance': candidate.get('relevance'),
            'frozenEvidence': candidate.get('frozenEvidence'),
            'deliveryApproved': candidate.get('deliveryApproved'),
            'highRelevance': candidate.get('highRelevance'),
            'petId': candidate.get('petId'),
        }
        # 并发递交（读路径投影与后台投影同时进行）时靠唯一键静默去重，不抛约束错误
        stmt = dialect_insert(session, PushDelivery).values(
            dedup_key=dedup_key(candidate['inboxItemId'], installation.installation_id),
            inbox_item_id=candidate['inboxItemId'],
            installation_id=installation.installation_id,
            user_id=user_id,
            platform=PLATFORM_IOS,
            category=category,
            state=STATE_PENDING,
            attempts=0,
            max_attempts=MAX_ATTEMPTS,
            next_attempt_at=now,
            token_digest=installation.token_digest,
            token_version=installation.token_version,
            meta=json.dumps(meta, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        ).on_conflict_do_nothing(index_elements=['dedup_key'])
        if (session.execute(stmt).rowcount or 0) == 1:
            published += 1
    return {'published': published, 'reason': None if published else 'no_eligible_installation',
            'category': category}


# ── 资格复核 ────────────────────────────────────────────────────────────────

def proactivity_level(session, user_id):
    """W09 主动程度（0=安静档）。"""
    return settings_service.load(session, user_id).get('proactivity', 1)


def evaluate_eligibility(session, delivery, installation, now=None):
    """复核安装实例侧资格。返回 ``(ok, reason)``；reason 为 None 表示通过。

    reason='platform_disabled' 表示闸门关闭——调用方应保留 pending 而不是作废。
    """
    if installation is None:
        return False, 'installation_missing'
    if delivery.platform != PLATFORM_IOS or installation.platform != PLATFORM_IOS:
        return False, 'platform_not_supported'
    if not platform_enabled(delivery.platform):
        return False, 'platform_disabled'
    if installation.user_id != delivery.user_id:
        return False, 'ownership_changed'
    if installation.state != INSTALLATION_ACTIVE:
        return False, 'installation_%s' % installation.state
    if not installation.expo_token:
        return False, 'token_missing'
    if installation.permission_status != PERMISSION_GRANTED:
        return False, 'permission_not_granted'
    if not user_enabled(session, delivery.user_id):
        return False, 'user_disabled'
    return True, None


def check_current_source(session, delivery, source):
    """按 W12 **当前**来源复核（不信任发布时的缓存 meta）。返回 ``(ok, reason)``。

    W12 的中心策略已经判过「是不是推送候选」（``source['pushCandidate']``），
    本函数**不重复推导**严重度、开放状态与证据门槛，只做 W13 自己的核对：

    - 来源已被删除/替换 → source_unavailable；
    - 历史补入 → backfilled；
    - 条目拥有者已变（换账号）→ ownership_changed；
    - 来源类型被换 → source_kind_changed；
    - 宠物已转走或被删 → pet_scope_changed；
    - 当前不再满足中心策略（告警已关闭、证据缺失等）→ not_push_candidate；
    - 个人变化且处于安静档 → quiet_mode（设备告警与到期回信不受安静档影响）。
    """
    if source is None:
        return False, 'source_unavailable'
    if source.get('backfilled'):
        return False, 'backfilled'
    owner_id = source.get('userId')
    if owner_id is None or int(owner_id) != delivery.user_id:
        return False, 'ownership_changed'
    if source.get('sourceKind') != delivery.category:
        return False, 'source_kind_changed'
    pet_owner = source.get('petOwnerId')
    if pet_owner is None or int(pet_owner) != delivery.user_id:
        return False, 'pet_scope_changed'
    if not source.get('pushCandidate'):
        return False, 'not_push_candidate'
    if delivery.category == CATEGORY_PERSONAL_CHANGE:
        if proactivity_level(session, delivery.user_id) <= 0:
            return False, 'quiet_mode'
    return True, None


# ── 投递执行 ────────────────────────────────────────────────────────────────

def _claim_for_dispatch(session, delivery_id, now):
    """CAS 领取：只有把 pending 改成 sending 成功的那一方才允许调用服务方。

    写前意图在独立事务里提交：进程在外部调用后、落库前中断时，行停在 sending，
    下次被识别为「结果未知」而停止重试（不会重复打扰用户）。
    """
    result = session.execute(
        update(PushDelivery)
        .where(PushDelivery.id == delivery_id, PushDelivery.state == STATE_PENDING)
        .values(state=STATE_SENDING, attempts=PushDelivery.attempts + 1, updated_at=now))
    return (result.rowcount or 0) == 1


def _release_claim(session, delivery_id, now):
    """闸门关闭时退回待发（不消耗重试次数）。"""
    row = session.get(PushDelivery, delivery_id)
    if row is None:
        return
    row.state = STATE_PENDING
    row.attempts = max(0, row.attempts - 1)
    row.next_attempt_at = now + timedelta(seconds=RETRY_BACKOFF_BASE_S)
    row.updated_at = now


def _backoff(attempts):
    return RETRY_BACKOFF_BASE_S * (2 ** max(0, attempts - 1))


def _finalize(session, delivery, state, reason=None, error_code=None, now=None, error=None):
    now = now or utc_now()
    delivery.state = state
    delivery.reason = reason
    if error_code:
        delivery.error_code = error_code
    delivery.updated_at = now
    if state in TERMINAL_STATES:
        delivery.finished_at = now
        delivery.next_attempt_at = None
    if error:
        delivery.last_error = push_provider.scrub(str(error))[:400]


def dispatch_delivery(delivery_id, now=None, provider=None):
    """投递一条待发件。返回结果摘要 dict。

    事务边界：领取、复核、落库各自独立成短事务；**调用服务方时不持有任何数据库
    事务**，避免长事务把库锁住。写前意图先提交，崩溃后可被识别为未知。
    """
    now = now or utc_now()
    provider = provider or push_provider
    # 预检：闸门关闭时保留 pending，不消耗重试次数
    with session_scope() as s:
        row = s.get(PushDelivery, delivery_id)
        if row is None or row.state != STATE_PENDING:
            return {'state': 'skipped', 'reason': 'not_pending'}
        if not platform_enabled(row.platform):
            return {'state': 'skipped', 'reason': 'platform_disabled'}
        if row.next_attempt_at is not None and row.next_attempt_at > now:
            return {'state': 'skipped', 'reason': 'not_due'}
        if (now - row.created_at).total_seconds() > PENDING_MAX_AGE_S:
            _finalize(s, row, STATE_SUPPRESSED, reason='expired', now=now)
            return {'state': STATE_SUPPRESSED, 'reason': 'expired'}
    # 领取（写前意图，独立事务提交）
    with session_scope() as s:
        if not _claim_for_dispatch(s, delivery_id, now):
            return {'state': 'skipped', 'reason': 'claimed_elsewhere'}
    # 复核资格与当前来源 → 构造载荷（事务在此结束）
    with session_scope() as s:
        delivery = s.get(PushDelivery, delivery_id)
        installation = s.execute(select(PushInstallation).where(
            PushInstallation.installation_id == delivery.installation_id)).scalar_one_or_none()
        ok, reason = evaluate_eligibility(s, delivery, installation, now=now)
        if ok:
            source = current_source(s, delivery.inbox_item_id)
            ok, reason = check_current_source(s, delivery, source)
        if not ok:
            if reason == 'platform_disabled':
                _release_claim(s, delivery_id, now)
                return {'state': 'skipped', 'reason': reason}
            _finalize(s, delivery, STATE_SUPPRESSED, reason=reason, now=now)
            return {'state': STATE_SUPPRESSED, 'reason': reason}
        message = build_message(installation.expo_token, delivery.category,
                                delivery.inbox_item_id)
        delivery.payload_digest = _digest({k: v for k, v in message.items() if k != 'to'})
        delivery.token_digest = installation.token_digest
        delivery.token_version = installation.token_version
    # 调用服务方：不在任何数据库事务内
    try:
        tickets = provider.send_messages([message])
    except push_provider.OutcomeUnknown as exc:
        with session_scope() as s:
            delivery = s.get(PushDelivery, delivery_id)
            _finalize(s, delivery, STATE_UNKNOWN, reason='outcome_unknown', now=now, error=exc)
        return {'state': STATE_UNKNOWN, 'reason': 'outcome_unknown'}
    except push_provider.ProviderError as exc:
        with session_scope() as s:
            delivery = s.get(PushDelivery, delivery_id)
            if exc.status == 429:
                return _schedule_retry(s, delivery, 'http_429', now)
            _finalize(s, delivery, STATE_FAILED, reason='provider_error',
                      error_code=exc.code, now=now, error=exc)
        return {'state': STATE_FAILED, 'reason': 'provider_error'}
    # 落库结果
    with session_scope() as s:
        delivery = s.get(PushDelivery, delivery_id)
        installation = s.execute(select(PushInstallation).where(
            PushInstallation.installation_id == delivery.installation_id)).scalar_one_or_none()
        ticket = tickets[0] if tickets else {'status': 'error', 'code': 'EmptyResponse',
                                             'id': None}
        if ticket.get('status') == 'ok':
            delivery.state = STATE_SENT
            delivery.expo_ticket_id = ticket.get('id')
            delivery.expo_receipt_id = ticket.get('id')
            delivery.ticket_status = 'ok'
            delivery.sent_at = now
            delivery.next_attempt_at = now + timedelta(seconds=RECEIPT_WAIT_S)
            delivery.updated_at = now
            delivery.last_error = None
            return {'state': STATE_SENT, 'ticketId': ticket.get('id')}
        code = ticket.get('code') or 'UnknownError'
        if code == push_provider.ERROR_DEVICE_NOT_REGISTERED:
            _apply_device_not_registered(s, delivery, installation, now=now)
            return {'state': STATE_REJECTED, 'reason': 'device_not_registered'}
        if code in push_provider.RECOVERABLE_ERRORS:
            return _schedule_retry(s, delivery, code, now)
        _finalize(s, delivery, STATE_FAILED, reason='ticket_rejected', error_code=code, now=now)
        return {'state': STATE_FAILED, 'reason': 'ticket_rejected', 'errorCode': code}


def _apply_device_not_registered(session, delivery, installation, now=None):
    """服务方判定 token 失效。

    **只在这次发送用的就是当前 token 时**才停用安装实例：若同一安装实例已经换上
    新 token，旧 token 的失效回执不得把新 token 一起废掉。
    """
    now = now or utc_now()
    same_token = (installation is not None
                  and bool(delivery.token_digest)
                  and delivery.token_digest == installation.token_digest
                  and (delivery.token_version or 0) == (installation.token_version or 0))
    if same_token:
        installation.state = INSTALLATION_UNREGISTERED
        installation.expo_token = ''
        _refresh_eligible_since(installation, now)
        installation.updated_at = now
    _finalize(session, delivery, STATE_REJECTED, reason='device_not_registered',
              error_code=push_provider.ERROR_DEVICE_NOT_REGISTERED, now=now)
    return same_token


def _schedule_retry(session, delivery, code, now):
    """明确可恢复的失败：有限重试；用尽次数则作废（站内仍可打开）。"""
    if delivery.attempts >= delivery.max_attempts:
        _finalize(session, delivery, STATE_FAILED, reason='retry_exhausted',
                  error_code=code, now=now)
        return {'state': STATE_FAILED, 'reason': 'retry_exhausted', 'errorCode': code}
    delivery.state = STATE_PENDING
    delivery.reason = 'retry_scheduled'
    delivery.error_code = code
    delivery.next_attempt_at = now + timedelta(seconds=_backoff(delivery.attempts))
    delivery.updated_at = now
    return {'state': STATE_PENDING, 'reason': 'retry_scheduled', 'errorCode': code}


def dispatch_due(now=None, limit=20, provider=None):
    """派发到期的待发件。返回处理条数（派发、跳过或抑制都算）。"""
    now = now or utc_now()
    with session_scope() as s:
        ids = s.execute(select(PushDelivery.id).where(
            PushDelivery.state == STATE_PENDING,
            (PushDelivery.next_attempt_at.is_(None)) | (PushDelivery.next_attempt_at <= now),
        ).order_by(PushDelivery.id).limit(limit)).scalars().all()
    for delivery_id in ids:
        dispatch_delivery(delivery_id, now=now, provider=provider)
    return len(ids)


def sweep_receipts(now=None, provider=None):
    """查回执并更新投递状态。返回处理条数。

    回执窗口 24 小时：超出窗口仍未拿到回执的按「未知」处理并停止重试。
    """
    now = now or utc_now()
    provider = provider or push_provider
    handled = 0
    with session_scope() as s:
        rows = s.execute(select(PushDelivery).where(
            PushDelivery.state == STATE_SENT,
            PushDelivery.expo_ticket_id.isnot(None),
            PushDelivery.next_attempt_at.isnot(None),
            PushDelivery.next_attempt_at <= now,
        ).order_by(PushDelivery.id).limit(RECEIPT_BATCH)).scalars().all()
        if not rows:
            return 0
        # 回执窗口外：不再等待，按未知处理（避免无期限占用重试额度）
        expired = [row for row in rows
                   if (now - (row.sent_at or row.created_at)).total_seconds() > RECEIPT_WINDOW_S]
        for row in expired:
            _finalize(s, row, STATE_UNKNOWN, reason='receipt_window_expired', now=now)
        handled += len(expired)
        rows = [row for row in rows if row.state == STATE_SENT]
        if not rows:
            return handled
        ticket_ids = [row.expo_ticket_id for row in rows]
    # 查询回执也是外部网络调用，不在数据库事务内等待。
    try:
        receipts = provider.get_receipts(ticket_ids)
    except (push_provider.OutcomeUnknown, push_provider.ProviderError):
        receipts = {}
    with session_scope() as s:
        rows = s.execute(select(PushDelivery).where(
            PushDelivery.state == STATE_SENT,
            PushDelivery.expo_ticket_id.in_(ticket_ids),
        )).scalars().all()
        for row in rows:
            receipt = receipts.get(row.expo_ticket_id)
            if receipt is None:
                row.next_attempt_at = now + timedelta(seconds=RECEIPT_POLL_INTERVAL_S)
                row.updated_at = now
                continue
            status = receipt.get('status')
            row.receipt_status = status
            if status in ('ok', 'delivered'):
                _finalize(s, row, STATE_DELIVERED, reason='delivered', now=now)
                handled += 1
                continue
            details = receipt.get('details') or {}
            code = details.get('error') if isinstance(details, dict) else None
            if code == push_provider.ERROR_RECEIPT_NOT_AVAILABLE:
                row.next_attempt_at = now + timedelta(seconds=RECEIPT_POLL_INTERVAL_S)
                row.updated_at = now
                continue
            if code == push_provider.ERROR_DEVICE_NOT_REGISTERED:
                installation = s.execute(select(PushInstallation).where(
                    PushInstallation.installation_id == row.installation_id)).scalar_one_or_none()
                _apply_device_not_registered(s, row, installation, now=now)
                handled += 1
                continue
            if code in push_provider.RECOVERABLE_ERRORS:
                _schedule_retry(s, row, code, now)
                handled += 1
                continue
            _finalize(s, row, STATE_FAILED, reason='receipt_error', error_code=code, now=now)
            handled += 1
    return handled


def recover_stuck(now=None):
    """把停在 sending 且已超时的行标记为结果未知。

    这些行对应「进程在外部调用之后、落库之前中断」：服务方是否接收未知，
    停止该条重试以免重复投递（spec：未知即停）。新近的 sending 行（可能正在
    同进程内派发）不动。
    """
    now = now or utc_now()
    threshold = now - timedelta(seconds=STUCK_SENDING_S)
    with session_scope() as s:
        rows = s.execute(select(PushDelivery).where(
            PushDelivery.state == STATE_SENDING,
            PushDelivery.updated_at <= threshold)).scalars().all()
        for row in rows:
            _finalize(s, row, STATE_UNKNOWN, reason='interrupted_after_dispatch', now=now)
        return len(rows)
