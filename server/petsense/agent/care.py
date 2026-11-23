"""W09 主动关怀域核心：主题持久化、硬件规则检测、学习完成转换与周期回顾调度
（agent-w09-spec.md §2/§3）。

事实与门槛由本模块确定性维护，模型只做表达与相关性（判断归模型、事实归代码）：
- 硬件规则沿用客户端配置语义：电量连续 3 个不同合格帧 <20% 开启（<10% 升级）、
  连续 3 帧 >25% 回稳；健康读数连续 90 个无效帧 ≈3 分钟；失联=连接可用且最后
  已收帧 >30 秒。帧去重由 telemetry.ingest_observation 的序号门保证——本模块只在
  接受的新帧上推进（旧帧/重复帧不打开新事件）；缺字段不计作明确无效。
- 事件状态持久化在 AgentCareTopic：相同实例只发一条首发消息，恢复更新同一主题，
  新实例须经过恢复或可审计关闭（换绑 closed_reason='rebind'）。
- 检测在本模块内保持轻量（可挂在 MQTT 入库事务里），事件运行（模型表达）由
  care_jobs 的调度 worker 执行，不阻塞实时分发。
- 失联判定依赖「可用连接 + 有效设备水位」：连接不可用（MQTT 故障/禁用/启动重连
  初期）不得编造失联事件；从未在线的设备无水位不判定。
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from petsense.db import iso_z, utc_now
from petsense.db.models import Device, Pet
from petsense.db.models.agent import AgentCareTopic, CareDeviceState, CarePetState

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo('Asia/Shanghai')

# 客户端配置语义（app/src/constants/alerts.ts）：阈值与帧数保持一致，D3 迁移后单一事实源在此
BATTERY_WARNING_BELOW = 20
BATTERY_CRITICAL_BELOW = 10
BATTERY_RESOLVE_ABOVE = 25
BATTERY_TRIGGER_FRAMES = 3
BATTERY_RESOLVE_FRAMES = 3
INVALID_TRIGGER_FRAMES = 90   # ≈3 分钟（按实际上报节拍计帧）
STALE_AFTER_S = 30            # 失联：最后已收帧超过 30 秒
STABLE_CONNECTION_S = 30      # 启动/重连保护：连接稳定满 30 秒后才判失联
EVIDENCE_LIMIT = 20           # 主题依据列表上限（保留最新）

# 受管理规则（后端已接管、新 App 应抑制本地重复提醒的三类）；其余告警仍由客户端自理
MANAGED_RULES = ('battery_low', 'data_stale', 'readings_invalid')

DEVICE_CATEGORIES = ('battery_low', 'data_stale', 'readings_invalid')
# 回顾发现类别白名单（服务端校验模型提案；未知归 other）
REVIEW_CATEGORIES = ('walk_volume', 'sound_events', 'coverage', 'observation', 'other')

CATEGORY_TITLES = {
    'battery_low': '项圈电量偏低',
    'data_stale': '项圈数据失联',
    'readings_invalid': '项圈读数持续无效',
    'learning_ready': '日常认识建立了',
}
SEVERITY_ORDER = {'info': 0, 'warning': 1, 'critical': 2}

REVIEW_DAILY_AT = (9, 0)     # 宠物可用时区每日 09:00 回顾昨天（D1）
REVIEW_WEEKLY_AT = (9, 15)   # 周一 09:15 回顾上一自然周（周一起点，窗口左闭右开）
SCAN_INTERVAL_S = 60


def pet_timezone(pet):
    """宠物可用时区：真实档案字段存在且有效时用它，否则 Asia/Shanghai（spec §2）。"""
    raw = getattr(pet, 'timezone', None)
    if isinstance(raw, str) and raw.strip():
        try:
            return ZoneInfo(raw.strip())
        except Exception:
            pass
    return SHANGHAI


def day_bounds(day_text, tz=SHANGHAI):
    """自然日边界（本地零点，左闭右开）→ UTC。"""
    start = datetime.strptime(day_text, '%Y-%m-%d').replace(tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def week_start(day_text, tz=SHANGHAI):
    """该自然日所在周的周一日期文本（周一起点）。"""
    local = datetime.strptime(day_text, '%Y-%m-%d').replace(tzinfo=tz)
    return (local - timedelta(days=local.weekday())).strftime('%Y-%m-%d')


def local_now(now, tz=SHANGHAI):
    return now.astimezone(tz)


def due_daily_window(now, tz=SHANGHAI):
    """最近的每日回顾机会：今天 09:00 已到 → 回顾昨天；否则昨天 09:00 → 回顾前天。

    返回 (day_text, scheduled_at_utc) 或 None（首启水位之前用）。只给出「最新受影响
    日窗口」（D4）：更早的窗口不补。
    """
    local = local_now(now, tz)
    today_due = local.replace(hour=REVIEW_DAILY_AT[0], minute=REVIEW_DAILY_AT[1],
                              second=0, microsecond=0)
    if local >= today_due:
        scheduled = today_due
    else:
        scheduled = today_due - timedelta(days=1)
    day = (scheduled - timedelta(days=1)).date().isoformat()
    return day, scheduled.astimezone(timezone.utc)


def due_weekly_window(now, tz=SHANGHAI):
    """最近的每周回顾机会：本周一 09:15 已到 → 回顾上一自然周；否则上周一 → 上上周。

    返回 (week_start_text, scheduled_at_utc)。服务器中断跨天时仍只补最新周窗口。
    """
    local = local_now(now, tz)
    this_monday = (local - timedelta(days=local.weekday())).replace(
        hour=REVIEW_WEEKLY_AT[0], minute=REVIEW_WEEKLY_AT[1], second=0, microsecond=0)
    if local >= this_monday:
        scheduled = this_monday
    else:
        scheduled = this_monday - timedelta(days=7)
    week = (scheduled - timedelta(days=7)).date().isoformat()
    return week, scheduled.astimezone(timezone.utc)


def weekly_bounds(week_text, tz=SHANGHAI):
    start = datetime.strptime(week_text, '%Y-%m-%d').replace(tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=7)).astimezone(timezone.utc)


# ── 主题持久化 ──────────────────────────────────────────────────────────────

def open_topic(session, pet_id, kind, category, anchor, *, severity='warning',
               title=None, detail='', evidence=None, now=None):
    """打开一个新事件实例；(pet,kind,category,anchor) 已有未恢复实例时返回 None（不重复开）。

    新实例须经过前一次恢复或可审计关闭：存在 open/ongoing 实例即拒绝新开。
    """
    now = now or utc_now()
    existing = session.execute(select(AgentCareTopic).where(
        AgentCareTopic.pet_id == pet_id, AgentCareTopic.kind == kind,
        AgentCareTopic.category == category, AgentCareTopic.anchor == anchor,
        AgentCareTopic.status.in_(('open', 'ongoing')))).scalar_one_or_none()
    if existing is not None:
        return None
    last = session.execute(select(AgentCareTopic).where(
        AgentCareTopic.pet_id == pet_id, AgentCareTopic.kind == kind,
        AgentCareTopic.category == category, AgentCareTopic.anchor == anchor)
        .order_by(AgentCareTopic.instance.desc()).limit(1)).scalar_one_or_none()
    row = AgentCareTopic(pet_id=pet_id, kind=kind, category=category, anchor=anchor,
                         instance=(last.instance + 1) if last else 1, status='open',
                         severity=severity, title=title or CATEGORY_TITLES.get(category, category),
                         detail=detail, evidence=json.dumps((evidence or [])[:EVIDENCE_LIMIT],
                                                            ensure_ascii=False, default=str),
                         opened_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return row


def topic_open_instance(session, pet_id, category, anchor):
    return session.execute(select(AgentCareTopic).where(
        AgentCareTopic.pet_id == pet_id, AgentCareTopic.category == category,
        AgentCareTopic.anchor == anchor,
        AgentCareTopic.status.in_(('open', 'ongoing')))).scalar_one_or_none()


def append_evidence(topic, entry):
    entries = json.loads(topic.evidence or '[]')
    entries.append(entry)
    topic.evidence = json.dumps(entries[-EVIDENCE_LIMIT:], ensure_ascii=False, default=str)
    topic.updated_at = utc_now()


def recover_topic(topic, now=None):
    """恢复：只更新同一主题（不发新首发消息）；幂等。"""
    if topic is None or topic.status in ('recovered', 'closed'):
        return False
    now = now or utc_now()
    topic.status = 'recovered'
    topic.recovered_at = now
    topic.updated_at = now
    return True


def close_topic(topic, reason, now=None):
    """可审计关闭（换绑等）：不算恢复，新实例可从此开启。"""
    if topic is None or topic.status in ('recovered', 'closed'):
        return False
    now = now or utc_now()
    topic.status = 'closed'
    topic.closed_reason = reason[:120]
    topic.updated_at = now
    return True


def escalate_topic(topic, severity, now=None):
    """升级（如电量 <10%）：更新严重度，保持同一实例。"""
    if topic is None:
        return False
    now = now or utc_now()
    if SEVERITY_ORDER.get(severity, 0) > SEVERITY_ORDER.get(topic.severity, 0):
        topic.severity = severity
    topic.status = 'ongoing'
    topic.updated_at = now
    return True


def public(topic):
    """主题的 App/工具共用序列化（证据随行，供「为什么提醒我」下钻）。"""
    return {'id': topic.id, 'kind': topic.kind, 'category': topic.category,
            'anchor': topic.anchor, 'instance': topic.instance, 'status': topic.status,
            'severity': topic.severity, 'title': topic.title, 'detail': topic.detail,
            'evidence': json.loads(topic.evidence or '[]'), 'delivered': topic.delivered,
            'openedAt': iso_z(topic.opened_at), 'updatedAt': iso_z(topic.updated_at),
            'recoveredAt': iso_z(topic.recovered_at) if topic.recovered_at else None,
            'closedReason': topic.closed_reason}


# ── 硬件规则检测（帧驱动部分） ───────────────────────────────────────────────

_replay_mode = {'active': False}


def set_replay_mode(active):
    """启动 backfill 重放历史帧时置 True：只推进检测水位，不为历史转换开题或入队。"""
    _replay_mode['active'] = bool(active)


def _battery_qualified(payload):
    """合格电量帧：battery.found 且 percent 为数值。"""
    battery = payload.get('battery')
    if not isinstance(battery, dict) or not battery.get('found'):
        return None
    percent = battery.get('percent')
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        return None
    return float(percent)


def observe_frame(session, device_id, owner_id, payload, received_at):
    """在已接受的新帧上推进硬件规则水位（telemetry.ingest_observation 调用）。

    只处理归属与绑定已生效的帧（ingest 已校验）；换绑（owner 变化）时把打开的主题
    按 rebind 关闭并重置水位，不编造事件。返回本帧产生的作业描述列表（由调用方入队，
    这里只组装 payload，不直接依赖 jobs 模块以便单独测试）：
    [{'topic': id, 'phase': 'open'|'escalate'}, ...]
    """
    row = session.get(CareDeviceState, device_id)
    if row is None:
        row = CareDeviceState(device_id=device_id, owner_id=owner_id, updated_at=received_at)
        session.add(row)
    elif row.owner_id != owner_id:
        # 换绑：旧主体的打开主题可审计关闭；检测水位按新归属从零开始
        _close_device_topics(session, row.owner_id, 'rebind', received_at)
        row.owner_id = owner_id
        row.battery_violation = row.battery_recovery = row.battery_critical = 0
        row.battery_first_at = None
        row.invalid_streak = 0
        row.invalid_first_at = None
        row.last_battery_percent = None
        row.updated_at = received_at
    pet_id = session.execute(select(Pet.id).where(
        Pet.user_id == owner_id, Pet.device_id == device_id)).scalar_one_or_none()
    triggered = []
    if pet_id is None:
        # 设备已认领但未关联宠物：只维护水位，不产生宠物事件
        row.last_frame_at = received_at
        row.frames_seen += 1
        row.updated_at = received_at
        return triggered

    frame_note = {'at': iso_z(received_at), 'kind': 'frame', 'battery': None, 'healthValid': None}

    # 电量规则（客户端语义 + 帧去重由 ingest 保证）
    percent = _battery_qualified(payload)
    if percent is not None:
        row.last_battery_percent = percent
        frame_note['battery'] = round(percent, 1)
        if percent < BATTERY_WARNING_BELOW:
            row.battery_violation += 1
            row.battery_recovery = 0
            if row.battery_first_at is None:
                row.battery_first_at = received_at
        else:
            row.battery_violation = 0
            row.battery_recovery = row.battery_recovery + 1 if percent > BATTERY_RESOLVE_ABOVE else 0
        open_row = None if _replay_mode['active'] else topic_open_instance(
            session, pet_id, 'battery_low', device_id)
        if _replay_mode['active']:
            pass  # 历史重放：水位已推进，不为旧转换开题/升级/恢复
        elif open_row is None and row.battery_violation >= BATTERY_TRIGGER_FRAMES:
            severity = 'critical' if percent < BATTERY_CRITICAL_BELOW else 'warning'
            opened = open_topic(session, pet_id, 'device', 'battery_low', device_id,
                                severity=severity, now=received_at,
                                evidence=[dict(frame_note, battery=round(percent, 1),
                                               note='首次越界 %s' % iso_z(row.battery_first_at)
                                               if row.battery_first_at else '')])
            if opened is not None:
                row.battery_critical = 1 if severity == 'critical' else 0
                triggered.append({'topic': opened.id, 'phase': 'open'})
        elif open_row is not None:
            if row.battery_recovery >= BATTERY_RESOLVE_FRAMES and percent > BATTERY_RESOLVE_ABOVE:
                if recover_topic(open_row, received_at):
                    append_evidence(open_row, {'at': iso_z(received_at), 'kind': 'recover',
                                               'battery': round(percent, 1)})
                    row.battery_violation = row.battery_recovery = 0
                    row.battery_first_at = None
                    row.battery_critical = 0
            else:
                escalate_topic(open_row, 'critical' if percent < BATTERY_CRITICAL_BELOW else 'warning',
                               received_at)
                append_evidence(open_row, frame_note)
                if percent < BATTERY_CRITICAL_BELOW and not row.battery_critical:
                    row.battery_critical = 1
                    triggered.append({'topic': open_row.id, 'phase': 'escalate'})

    # 长期无效读数规则：缺字段不计作明确无效（既不累积也不回稳）
    health = payload.get('health')
    if isinstance(health, dict) and 'valid' in health:
        frame_note['healthValid'] = bool(health.get('valid'))
        if health.get('valid') is True:
            row.invalid_streak = 0
            row.invalid_first_at = None
            invalid_row = None if _replay_mode['active'] else topic_open_instance(
                session, pet_id, 'readings_invalid', device_id)
            if invalid_row is not None and recover_topic(invalid_row, received_at):
                append_evidence(invalid_row, {'at': iso_z(received_at), 'kind': 'recover',
                                              'healthValid': True})
        else:
            row.invalid_streak += 1
            if row.invalid_first_at is None:
                row.invalid_first_at = received_at
            invalid_row = None if _replay_mode['active'] else topic_open_instance(
                session, pet_id, 'readings_invalid', device_id)
            if not _replay_mode['active'] and invalid_row is None \
                    and row.invalid_streak >= INVALID_TRIGGER_FRAMES:
                opened = open_topic(session, pet_id, 'device', 'readings_invalid', device_id,
                                    severity='warning', now=received_at,
                                    evidence=[{'at': iso_z(received_at), 'kind': 'frame',
                                               'healthValid': False,
                                               'note': '连续无效第 %d 帧，首次 %s' % (
                                                   row.invalid_streak,
                                                   iso_z(row.invalid_first_at or received_at))}])
                if opened is not None:
                    triggered.append({'topic': opened.id, 'phase': 'open'})
            elif invalid_row is not None:
                append_evidence(invalid_row, frame_note)

    # 失联恢复：任何新合格帧即恢复（帧到达本身证明链路恢复）；回放期间无主题可恢复
    stale_row = None if _replay_mode['active'] else topic_open_instance(
        session, pet_id, 'data_stale', device_id)
    if stale_row is not None and recover_topic(stale_row, received_at):
        append_evidence(stale_row, {'at': iso_z(received_at), 'kind': 'recover',
                                    'note': '重新收到设备帧'})

    row.last_frame_at = received_at
    row.frames_seen += 1
    row.updated_at = received_at
    return triggered


def _close_device_topics(session, owner_id, reason, now):
    """把该归属下所有设备类打开主题关闭（换绑等场景）。"""
    pet_ids = session.execute(select(Pet.id).where(Pet.user_id == owner_id)).scalars().all()
    if not pet_ids:
        return
    rows = session.execute(select(AgentCareTopic).where(
        AgentCareTopic.pet_id.in_(pet_ids), AgentCareTopic.kind == 'device',
        AgentCareTopic.status.in_(('open', 'ongoing')))).scalars().all()
    for row in rows:
        close_topic(row, reason, now)


def observe_frame_and_queue(session, device_id, owner_id, payload, received_at):
    """telemetry 入库事务内的检测钩子：推进水位并把触发的运行入队（同事务提交）。

    检测本身轻量（状态机 + 少量行写），事件运行（模型表达）走持久作业由调度
    worker 执行，不阻塞 MQTT 实时分发（spec §3）。异常不破坏遥测入库。
    """
    try:
        triggered = observe_frame(session, device_id, owner_id, payload, received_at)
        if triggered:
            from petsense.agent import jobs
            for item in triggered:
                jobs.enqueue(session, 'care_event',
                             {'topic': item['topic'], 'phase': item['phase']},
                             dedup_key='care:event:topic:%d:%s' % (item['topic'], item['phase']),
                             next_run_at=received_at)
    except Exception:
        logger.exception('care observe_frame failed for device %s', device_id)


# ── 失联检测（扫描驱动部分） ────────────────────────────────────────────────

_connection_state = {'connected_since': None}


def mqtt_connection_ready(now=None):
    """云端 MQTT 连接是否可用且已稳定（启动/重连保护）。测试可 patch 本函数。"""
    from petsense.telemetry import mqtt_ingest
    try:
        connected = bool(mqtt_ingest.mqtt_client.is_connected())
    except Exception:
        return False
    if not connected:
        _connection_state['connected_since'] = None
        return False
    now = now or utc_now()
    if _connection_state['connected_since'] is None:
        _connection_state['connected_since'] = now
        return False  # 本 tick 只记录连接起点，下个稳定 tick 才判失联
    return (now - _connection_state['connected_since']).total_seconds() >= STABLE_CONNECTION_S


def detect_stale(session, now, connection_ready=None):
    """扫描失联：连接可用且最后已收帧 >30 秒才判定；返回产生的作业描述列表。

    从未在线（无水位/无帧）不判定；连接不可用（MQTT 故障、禁用、启动重连）不编造。
    """
    connection_ready = mqtt_connection_ready if connection_ready is None else connection_ready
    triggered = []
    if not connection_ready(now):
        return triggered
    states = session.execute(select(CareDeviceState)).scalars().all()
    for row in states:
        if row.last_frame_at is None:
            continue
        if (now - row.last_frame_at).total_seconds() <= STALE_AFTER_S:
            continue
        pet_id = session.execute(select(Pet.id).where(
            Pet.user_id == row.owner_id, Pet.device_id == row.device_id)).scalar_one_or_none()
        if pet_id is None:
            continue
        if topic_open_instance(session, pet_id, 'data_stale', row.device_id) is not None:
            continue
        opened = open_topic(session, pet_id, 'device', 'data_stale', row.device_id,
                            severity='info', now=now,
                            evidence=[{'at': iso_z(now), 'kind': 'scan',
                                       'note': '最后帧 %s（%.0f 秒前），连接可用'
                                               % (iso_z(row.last_frame_at),
                                                  (now - row.last_frame_at).total_seconds())}])
        if opened is not None:
            triggered.append({'topic': opened.id, 'phase': 'open'})
    return triggered


# ── 学习完成转换（W05 → W09 事件） ─────────────────────────────────────────

def learning_events(session, pet, now=None):
    """对比 W05 学习状态水位：真实 learning→ready 转换产生一次学习完成事件。

    首次观察只初始化水位（生产首启不把历史已就绪当新事件）；键=宠物+维度+版本，
    同键终身一次，新版本重新累计后可再次报告。返回产生的作业描述列表。
    """
    from petsense.agent.baseline import dimension_states
    now = now or utc_now()
    state = session.get(CarePetState, pet.id)
    watermark = json.loads(state.learning_watermark or '{}') if state else {}
    current = {}
    for dim in dimension_states(session, pet):
        if dim.get('active'):
            current['%s:%s' % (dim['dimension'], dim['version'])] = dim['state']
    triggered = []
    for key, status in current.items():
        previous = watermark.get(key)
        if previous is None or previous == status:
            continue
        if status != 'ready':
            continue
        feature = key.split(':', 1)[0]
        opened = open_topic(session, pet.id, 'learning', 'learning_ready', key,
                            severity='info', title=CATEGORY_TITLES['learning_ready'],
                            detail='维度 %s 已达到 %d 个有效日' % (feature, 7), now=now,
                            evidence=[{'at': iso_z(now), 'kind': 'scan', 'dimension': key,
                                       'from': previous, 'to': status}])
        if opened is not None:
            triggered.append({'topic': opened.id, 'phase': 'open'})
    if current != watermark:
        if state is None:
            state = CarePetState(pet_id=pet.id, enabled_at=now, updated_at=now)
            session.add(state)
        state.learning_watermark = json.dumps(current, ensure_ascii=False)
        state.updated_at = now
    return triggered


# ── 周期回顾调度（D1/D4） ───────────────────────────────────────────────────

def ensure_pet_state(session, pet, now=None):
    """首次看到该宠时落启用水位：早于 enabled_at 的调度点不补跑。"""
    now = now or utc_now()
    row = session.get(CarePetState, pet.id)
    if row is None:
        row = CarePetState(pet_id=pet.id, enabled_at=now, updated_at=now)
        session.add(row)
        session.flush()
    return row


def due_reviews(session, pet, now):
    """计算该宠当前应补的最新回顾窗口（日/周各至多一个，D4）。

    返回 [{'kind','key','windowStart','windowEnd','scheduledAt'}]；调度点早于
    启用水位的跳过（首次启用从下一调度点开始）。
    """
    state = session.get(CarePetState, pet.id)
    if state is None:
        return []
    tz = pet_timezone(pet)
    plans = []
    day, scheduled = due_daily_window(now, tz)
    if scheduled >= state.enabled_at:
        start, end = day_bounds(day, tz)
        plans.append({'kind': 'daily', 'key': day, 'windowStart': start, 'windowEnd': end,
                      'scheduledAt': scheduled})
    week, week_scheduled = due_weekly_window(now, tz)
    if week_scheduled >= state.enabled_at:
        start, end = weekly_bounds(week, tz)
        plans.append({'kind': 'weekly', 'key': week, 'windowStart': start, 'windowEnd': end,
                      'scheduledAt': week_scheduled})
    return plans


def device_bound_pet(session, pet):
    """调度资格：绑定了设备的宠物（观测都经设备而来，spec §2 D1）。"""
    return bool(pet.device_id)


# ── 事件事实组装（供事件运行与降级文案） ─────────────────────────────────────

def event_facts(topic, state_row=None):
    """事件运行的确定性事实与来源（模型只做表达，不得改动事实）。"""
    facts = {'topicId': topic.id, 'kind': topic.kind, 'category': topic.category,
             'status': topic.status, 'severity': topic.severity, 'title': topic.title,
             'detail': topic.detail, 'openedAt': iso_z(topic.opened_at),
             'instance': topic.instance, 'evidence': json.loads(topic.evidence or '[]')}
    if state_row is not None:
        facts['device'] = {'lastFrameAt': iso_z(state_row.last_frame_at)
                           if state_row.last_frame_at else None,
                           'lastBatteryPercent': state_row.last_battery_percent,
                           'framesSeen': state_row.frames_seen}
    return facts


def deterministic_event_text(topic, phase):
    """模型不可用时的降级文案（事实句，不含推断；W04 facts-only 同语义）。"""
    if topic.category == 'battery_low':
        percent = topic_last_value(topic, 'battery')
        value = '，最近读数约 %d%%' % percent if percent is not None else ''
        if phase == 'escalate':
            return '项圈电量已经很低了%s，建议尽快充电，避免记录中断。' % value
        return '项圈电量偏低%s，方便时帮它充下电。' % value
    if topic.category == 'data_stale':
        return '有一阵子没收到项圈的新数据了，可以看看项圈是否在正常工作。'
    if topic.category == 'readings_invalid':
        return '项圈的健康读数持续无效约 3 分钟，可能没有戴稳，可以检查一下佩戴。'
    if topic.category == 'learning_ready':
        return '连续几天的观察凑够了：照料助手已经开始认识它的日常了。'
    return topic.title


def topic_last_value(topic, field):
    """从依据里取最近一次出现的数值字段（降级文案用；None 表示没有）。"""
    for entry in reversed(json.loads(topic.evidence or '[]')):
        if isinstance(entry, dict) and isinstance(entry.get(field), (int, float)):
            return entry[field]
    return None
