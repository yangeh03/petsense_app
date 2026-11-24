"""W12 收件箱索引：稳定来源引用、计数、预览、已读与深链解析（agent-w12-spec.md）。

设计要点（对齐 Codex 复核意见）：
- **投影而非改写来源**：W09 的 care.py / W08 的 tasks.py 由其他包并行开发，本包不改
  它们，改为「可恢复的投影水位」——每宠物一行 AgentInboxState 记启用水位与两个 id
  水位（主题、回信），条目唯一键 (宠物, 来源类型, 稳定事件键) + on_conflict_do_nothing
  保证重复投影、并发写入与重启恢复都不重复。
- **索引只存引用**：条目表不复制来源正文（D4），标题/摘要一律在读取时从来源水合；
  来源不可访问时给出明确的安全历史状态，不泄露原绑定内容（换绑关闭的主题不水合）。
- **精确的来源→回合身份**：W09 的真实 request id 是 `care:event:<topic>:<phase>` 与
  `care:review:<daily|weekly>:<pet>:<窗口>`，这里按**精确 id** 匹配（不做前缀 LIKE），
  并把命中的回合 id 持久化在条目上（turn_id），重开/升级阶段各自可区分。
- **发布状态可后补**：水位推进可能早于 W09 发布，故每次投影都做一次**富化**：按精确
  request id 重新解析回合身份、刷新 published/状态/策略判定，保留稳定 id、创建时间与
  已读状态（已读不被复活）。历史补入不追溯推送。
- **中心策略**：预览档位与 W13 推送候选都读 `inbox_eligibility` 的同一份策略输入。
"""
import json
import logging

from sqlalchemy import select

from petsense.agent.inbox_eligibility import (
    DELIVERY_KINDS,
    PRIORITY_CARE,
    PRIORITY_DEVICE,
    PRIORITY_LEARNING,
    PRIORITY_REPLY,
    SOURCE_CARE,
    SOURCE_DEVICE_FACT,
    SOURCE_KINDS,
    SOURCE_LEARNING,
    SOURCE_REPLY,
    TOPIC_KIND_TO_SOURCE,
    Eligibility,
    event_request_ids,
    policy_input,
    preview_tier,
    push_candidate,
    review_request_ids,
)
from petsense.db import dialect_insert, iso_z, utc_now
from petsense.db.models import Pet
from petsense.db.models.agent import (
    AgentCareTopic,
    AgentInboxItem,
    AgentInboxState,
    AgentTask,
    AgentTaskDelivery,
    AgentTurn,
)

logger = logging.getLogger(__name__)

PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 50
ENRICH_LIMIT = 200          # 单次富化扫描的条目上限
SCAN_LIMIT = 200            # 单次投影扫描的来源上限

# 来源状态（显式安全历史）
SOURCE_AVAILABLE = 'available'
SOURCE_UNAVAILABLE = 'unavailable'
SOURCE_OUT_OF_SCOPE = 'out_of_scope'


# ── 状态与条目 ─────────────────────────────────────────────────────────────

def ensure_state(session, pet_id, now):
    """取（或建）该宠的投影状态行：并发下用 upsert 兜底，避免唯一键冲突。"""
    row = session.get(AgentInboxState, pet_id)
    if row is not None:
        return row, False
    stmt = dialect_insert(session, AgentInboxState).values(
        pet_id=pet_id, enabled_at=now, care_watermark=0, delivery_watermark=0, updated_at=now)
    stmt = stmt.on_conflict_do_nothing(index_elements=['pet_id'])
    session.execute(stmt)
    return session.get(AgentInboxState, pet_id), True


def _insert_item(session, pet, source_kind, event_key, *, source_ref, eligibility,
                 backfilled=False, now=None):
    """写入一条收件箱条目（幂等）。返回 (item, created)。"""
    now = now or utc_now()
    values = {'user_id': pet.user_id, 'pet_id': pet.id, 'source_kind': source_kind,
              'event_key': event_key,
              'source_ref': json.dumps(source_ref, ensure_ascii=False, default=str),
              'priority': eligibility.priority,
              'previewable': 1 if eligibility.previewable else 0,
              'published': 1 if eligibility.published else 0,
              'turn_id': eligibility.turn_id,
              'relevance': eligibility.relevance or '',
              'frozen_evidence': 1 if eligibility.frozen_evidence else 0,
              'delivery_approved': 1 if eligibility.delivery_approved else 0,
              'high_relevance': 1 if eligibility.high_relevance else 0,
              'push_candidate': 1 if eligibility.push_candidate else 0,
              'status': SOURCE_AVAILABLE,
              'backfilled': 1 if backfilled else 0,
              'read_at': None, 'created_at': now, 'updated_at': now}
    stmt = dialect_insert(session, AgentInboxItem).values(**values)
    stmt = stmt.on_conflict_do_nothing(index_elements=['pet_id', 'source_kind', 'event_key'])
    result = session.execute(stmt)
    if result.rowcount == 1:
        return session.get(AgentInboxItem, result.inserted_primary_key[0]), True
    existing = session.execute(select(AgentInboxItem).where(
        AgentInboxItem.pet_id == pet.id, AgentInboxItem.source_kind == source_kind,
        AgentInboxItem.event_key == event_key)).scalar_one()
    return existing, False


def _eligibility_of(item) -> Eligibility:
    return Eligibility(source_kind=item.source_kind, priority=item.priority,
                       previewable=bool(item.previewable), published=bool(item.published),
                       turn_id=item.turn_id, relevance=item.relevance or '',
                       frozen_evidence=bool(item.frozen_evidence),
                       delivery_approved=bool(item.delivery_approved),
                       high_relevance=bool(item.high_relevance),
                       push_candidate=bool(item.push_candidate),
                       backfilled=bool(item.backfilled))


def _store_eligibility(session, item, eligibility, now=None):
    """把资格快照写回条目（不触碰 read_at / created_at）。"""
    now = now or utc_now()
    item.priority = eligibility.priority
    item.previewable = 1 if eligibility.previewable else 0
    item.published = 1 if eligibility.published else 0
    item.turn_id = eligibility.turn_id
    item.relevance = eligibility.relevance or ''
    item.frozen_evidence = 1 if eligibility.frozen_evidence else 0
    item.delivery_approved = 1 if eligibility.delivery_approved else 0
    item.high_relevance = 1 if eligibility.high_relevance else 0
    item.push_candidate = 1 if eligibility.push_candidate else 0
    item.updated_at = now


# ── W13 发布缝：条目首次成为推送候选时入队（单向：inbox → push） ────────────

def push_facts(session, item):
    """按来源**当前**状态生成 W13 发布缝入参（push.ITEM_CONTRACT 形状）。

    是否可推（``pushCandidate``）由中心策略按实时来源状态判定，与今日预览同口径。
    """
    policy = _live_policy(session, item)
    return {
        'id': str(item.id),
        'userId': item.user_id,
        'petId': item.pet_id,
        'kind': item.source_kind,
        'pushCandidate': bool(policy.push_candidate()),
        'severity': policy.severity or '',
        'sourceStatus': policy.source_status or '',
        'relevance': policy.relevance,
        'frozenEvidence': policy.frozen_evidence,
        'deliveryApproved': policy.delivery_approved,
        'highRelevance': bool(policy.frozen_evidence and policy.relevance == 'high'
                              and policy.delivery_approved),
        'backfilled': bool(item.backfilled),
        'createdAt': iso_z(item.created_at),
    }


def _push_source(session, inbox_item_id):
    """W13 发送前复核用的「当前来源」解析器（push.SOURCE_CONTRACT 形状）。

    返回 None 表示条目已不存在（来源不可用）。宠物被转走/删除时 ``petOwnerId``
    与 ``userId`` 不同/为空，W13 据此判定 pet_scope_changed。
    """
    try:
        item_id = int(inbox_item_id)
    except (TypeError, ValueError):
        return None
    item = session.get(AgentInboxItem, item_id)
    if item is None:
        return None
    pet = session.get(Pet, item.pet_id)
    policy = _live_policy(session, item)
    return {
        'userId': item.user_id,
        'petId': item.pet_id,
        'petOwnerId': pet.user_id if pet is not None else None,
        'sourceKind': item.source_kind,
        'backfilled': bool(item.backfilled),
        'published': bool(policy.published),
        'severity': policy.severity or '',
        'sourceStatus': policy.source_status or '',
        'relevance': policy.relevance,
        'frozenEvidence': policy.frozen_evidence,
        'deliveryApproved': policy.delivery_approved,
        'highRelevance': bool(policy.frozen_evidence and policy.relevance == 'high'
                              and policy.delivery_approved),
        'pushCandidate': bool(policy.push_candidate()),
    }


def install_push_bridge():
    """把当前来源解析器安装到 W13（app 启动时调用一次；幂等）。

    只把 W12 的读取函数交给 W13，不引入反向依赖；未安装时 W13 发送前复核一律
    保守抑制（宁可少发，不错发）。
    """
    from petsense.agent import push
    push.set_inbox_source(_push_source)


def _maybe_publish_push(session, item, *, was_candidate):
    """条目**首次**成为推送候选时在同一事务里入队（False → True 迁移触发）。

    - 已候选条目的重复投影、富化与已候选状态下的重读都不再触发；W13 侧再按
      (收件箱条目, 安装实例) 幂等去重，双保险保证「入队恰好一次」。
    - 历史补入条目中心策略恒为非候选，天然不会迁移；闸门关闭时 W13 不建行，
      开闸后也只推「新事件窗口」内的条目，不补发历史。
    - 安静档与闸门关闭是**延迟而非作废**：入队被这两类原因挡下时把候选位清回
      未入队，安静结束/开闸后的下一次投影会重新入队（仍受新事件窗口约束）。
    """
    try:
        from petsense.agent import push
    except Exception:  # pragma: no cover - 推送域不可用时收件箱仍要工作
        return
    facts = push_facts(session, item)
    if facts['pushCandidate'] and not was_candidate:
        result = push.publish_inbox_item(session, facts)
        if not result['published'] and result.get('reason') in ('quiet_mode',
                                                                'platform_disabled'):
            item.push_candidate = 0



# ── 精确的来源→回合身份 ───────────────────────────────────────────────────

def _resolve_turn_id(session, pet_id, topic=None, delivery=None):
    """按 W09 的**真实 request id** 精确解析来源对应的交付回合。

    - 设备/学习主题：W09 事件交付的 key 是 `care:event:<topic>:<phase>`，phase ∈
      {open, escalate}；两者都精确比对（不用前缀 LIKE，避免重开/升级阶段误配）。
    - 关怀（回顾发现）主题：W09 回顾交付的 key 是 `care:review:<daily|weekly>:<pet>:<窗口>`，
      窗口键取主题依据里的 review 条目；日/周两种 key 都精确试一次。
    - 到期回信：交付行自带 turn_id（W08 在同一事务写入）。
    """
    if delivery is not None:
        return delivery.turn_id
    if topic is None:
        return None
    if topic.kind in ('device', 'learning'):
        keys = event_request_ids(topic.id)
    elif topic.kind == 'observation':
        keys = []
        for entry in json.loads(topic.evidence or '[]'):
            window = entry.get('window') if isinstance(entry, dict) else None
            if window:
                keys.extend(review_request_ids(pet_id, window))
        if not keys:
            return None
    else:
        return None
    rows = session.execute(select(AgentTurn.id).where(
        AgentTurn.pet_id == pet_id, AgentTurn.request_id.in_(keys))
        .order_by(AgentTurn.id.desc())).scalars().all()
    return rows[0] if rows else None


# ── 来源水合（读时取正文，不复制进索引表） ───────────────────────────────

def hydrate(session, item):
    """从来源读取正文与状态。返回 (fields, detail)。

    fields: {'title','summary','severity','sourceStatus','available'}
    detail: 结构化来源详情（供下钻）
    """
    ref = json.loads(item.source_ref or '{}')
    if item.source_kind in (SOURCE_DEVICE_FACT, SOURCE_CARE, SOURCE_LEARNING):
        topic = session.get(AgentCareTopic, ref.get('topicId'))
        if topic is None:
            return ({'title': '', 'summary': '', 'severity': '', 'sourceStatus': '',
                     'available': False},
                    {'status': SOURCE_UNAVAILABLE, 'reason': 'source_unavailable', 'topic': None})
        if topic.pet_id != item.pet_id:
            # 来源与条目不在同一宠物下：不出内容（防止越权与旧绑定泄露）
            return ({'title': '', 'summary': '', 'severity': '', 'sourceStatus': '',
                     'available': False},
                    {'status': SOURCE_OUT_OF_SCOPE, 'reason': 'pet_mismatch', 'topic': None})
        if topic.status == 'closed' and topic.closed_reason == 'rebind':
            # 换绑关闭：设备归属已变，不水合原绑定内容（只留安全历史）
            return ({'title': '', 'summary': '', 'severity': topic.severity,
                     'sourceStatus': topic.status, 'available': False},
                    {'status': SOURCE_OUT_OF_SCOPE, 'reason': 'rebind', 'topic': None})
        return ({'title': topic.title, 'summary': topic.detail, 'severity': topic.severity,
                 'sourceStatus': topic.status, 'available': True},
                {'status': topic.status, 'reason': '', 'topic': {
                    'id': topic.id, 'kind': topic.kind, 'category': topic.category,
                    'title': topic.title, 'detail': topic.detail, 'severity': topic.severity,
                    'evidence': json.loads(topic.evidence or '[]'),
                    'openedAt': iso_z(topic.opened_at), 'updatedAt': iso_z(topic.updated_at)}})
    if item.source_kind == SOURCE_REPLY:
        delivery = session.get(AgentTaskDelivery, ref.get('deliveryId'))
        if delivery is None:
            return ({'title': '', 'summary': '', 'severity': '', 'sourceStatus': '',
                     'available': False},
                    {'status': SOURCE_UNAVAILABLE, 'reason': 'source_unavailable',
                     'delivery': None})
        task = session.get(AgentTask, delivery.task_id)
        if task is None or task.pet_id != item.pet_id:
            return ({'title': '', 'summary': '', 'severity': '', 'sourceStatus': '',
                     'available': False},
                    {'status': SOURCE_OUT_OF_SCOPE, 'reason': 'pet_mismatch',
                     'delivery': None})
        payload = {}
        try:
            payload = json.loads(delivery.payload or '{}')
        except ValueError:
            payload = {}
        title = '到期回信'
        try:
            from petsense.agent import tasks as task_service
            plan = task_service.plan_of(session, task)
            title = str(plan.get('title') or title)[:80]
        except Exception:  # noqa: BLE001 计划不可读时用默认标题
            pass
        return ({'title': title, 'summary': str(payload.get('text') or ''), 'severity': '',
                 'sourceStatus': 'delivered', 'available': True},
                {'status': 'delivered', 'reason': '', 'delivery': {
                    'id': delivery.id, 'kind': delivery.kind, 'taskId': delivery.task_id,
                    'planVersion': delivery.plan_version, 'turnId': delivery.turn_id,
                    'text': payload.get('text') or '',
                    'windowStart': payload.get('windowStart'),
                    'windowEnd': payload.get('windowEnd'),
                    'createdAt': iso_z(delivery.created_at)}})
    return ({'title': '', 'summary': '', 'severity': '', 'sourceStatus': '', 'available': False},
            {'status': SOURCE_UNAVAILABLE, 'reason': 'unknown_source'})


# ── 投影 ──────────────────────────────────────────────────────────────────

def _publication_inputs(session, topic):
    """W11→W12 已确认发布资格的三项输入（相关性 / 冻结证据 / 交付批准）。

    判据全在 `inbox_publication`（严格解析不可变运行轨迹）；不成立时返回空值，
    绝不默认按 high 处理。
    """
    from petsense.agent import inbox_publication
    return inbox_publication.publication_inputs(session, topic)


def _project_topic(session, pet, topic, *, backfilled=False, now=None):
    """把一个主题投影成收件箱条目（幂等；严重度在键里，升级阶段是新条目）。"""
    source_kind = TOPIC_KIND_TO_SOURCE.get(topic.kind)
    if source_kind is None:
        return False
    turn_id = _resolve_turn_id(session, pet.id, topic=topic)
    relevance, frozen, approved = _publication_inputs(session, topic)
    eligibility = Eligibility(
        source_kind=source_kind,
        priority={SOURCE_DEVICE_FACT: PRIORITY_DEVICE, SOURCE_CARE: PRIORITY_CARE,
                  SOURCE_LEARNING: PRIORITY_LEARNING}[source_kind],
        published=turn_id is not None, turn_id=turn_id, backfilled=backfilled,
        relevance=relevance, frozen_evidence=frozen, delivery_approved=approved,
        reason='topic:%s' % topic.kind).with_personal()
    if source_kind == SOURCE_LEARNING:
        event_key = 'learning:%s' % topic.anchor          # 学习完成沿用维度+版本
    elif source_kind == SOURCE_DEVICE_FACT:
        event_key = 'topic:%d:%s' % (topic.id, topic.severity)
    else:
        event_key = 'topic:%d' % topic.id
    source_ref = {'topicId': topic.id, 'kind': topic.kind, 'category': topic.category,
                  'anchor': topic.anchor, 'instance': topic.instance, 'runId': topic.run_id}
    _item, created = _insert_item(session, pet, source_kind, event_key,
                                  source_ref=source_ref, eligibility=eligibility,
                                  backfilled=backfilled, now=now)
    return created


def _project_delivery(session, pet, delivery, now=None):
    """把一条到期回信投影成收件箱条目（按持久交付引用去重）。"""
    if delivery.kind not in DELIVERY_KINDS:
        return False
    eligibility = Eligibility(source_kind=SOURCE_REPLY, priority=PRIORITY_REPLY,
                              published=delivery.turn_id is not None,
                              turn_id=delivery.turn_id, reason='delivery:%s' % delivery.kind)
    source_ref = {'deliveryId': delivery.id, 'taskId': delivery.task_id,
                  'planVersion': delivery.plan_version, 'kind': delivery.kind,
                  'instanceKey': delivery.instance_key}
    _item, created = _insert_item(session, pet, SOURCE_REPLY, 'delivery:%d' % delivery.id,
                                  source_ref=source_ref, eligibility=eligibility, now=now)
    return created


def project_source(session, pet, source, *, now=None):
    """事务内投影入口（W09/W11 合并用）：把来源投影成收件箱条目（幂等）。"""
    if isinstance(source, AgentCareTopic):
        return _project_topic(session, pet, source, now=now)
    if isinstance(source, AgentTaskDelivery):
        return _project_delivery(session, pet, source, now=now)
    return False


def _enrich_item(session, item, now=None):
    """富化：重新解析精确的来源→回合身份并刷新策略判定（不复活已读、不改 id）。"""
    now = now or utc_now()
    was_candidate = bool(item.push_candidate)
    eligibility = _eligibility_of(item)
    ref = json.loads(item.source_ref or '{}')
    turn_id = None
    severity = ''
    source_status = ''
    if item.source_kind == SOURCE_REPLY:
        delivery = session.get(AgentTaskDelivery, ref.get('deliveryId'))
        if delivery is not None:
            turn_id = delivery.turn_id
            source_status = 'delivered'
    else:
        topic = session.get(AgentCareTopic, ref.get('topicId'))
        if topic is not None:
            turn_id = _resolve_turn_id(session, item.pet_id, topic=topic)
            severity = topic.severity
            source_status = topic.status
    # W11→W12：已确认发布资格（严格依据不可变运行轨迹）。解析不出来就**保持原值**，
    # 既不抬升也不降级——正常/默认高相关、安静档、证据缺失都不成立。
    relevance, frozen, approved = eligibility.relevance, eligibility.frozen_evidence, \
        eligibility.delivery_approved
    if item.source_kind == SOURCE_CARE:
        topic = session.get(AgentCareTopic, ref.get('topicId'))
        if topic is not None:
            found_relevance, found_frozen, found_approved = _publication_inputs(session, topic)
            if found_frozen:
                relevance, frozen, approved = found_relevance, found_frozen, found_approved
    updated = Eligibility(source_kind=eligibility.source_kind, priority=eligibility.priority,
                          previewable=eligibility.previewable,
                          published=turn_id is not None, turn_id=turn_id,
                          relevance=relevance,
                          frozen_evidence=frozen,
                          delivery_approved=approved,
                          high_relevance=eligibility.high_relevance,
                          backfilled=eligibility.backfilled, reason=eligibility.reason)
    policy = policy_input(updated, severity=severity, source_status=source_status)
    tier = preview_tier(policy)
    updated = Eligibility(source_kind=updated.source_kind,
                          priority=updated.priority,
                          previewable=tier is not None,
                          published=updated.published, turn_id=updated.turn_id,
                          relevance=updated.relevance,
                          frozen_evidence=updated.frozen_evidence,
                          delivery_approved=updated.delivery_approved,
                          high_relevance=updated.frozen_evidence
                          and updated.relevance == 'high' and updated.delivery_approved,
                          push_candidate=push_candidate(policy),
                          backfilled=updated.backfilled, reason=updated.reason)
    _store_eligibility(session, item, updated, now=now)
    _maybe_publish_push(session, item, was_candidate=was_candidate)
    return updated


def project_pet(session, pet, now=None):
    """投影一个宠物的来源到收件箱，并富化既有条目。返回新建条目数。"""
    now = now or utc_now()
    state, fresh = ensure_state(session, pet.id, now)
    created = 0
    if fresh:
        # 首次投影 = 启用：只补当下仍开放的设备告警（D5），其余来源从启用后入箱
        topics = session.execute(select(AgentCareTopic).where(
            AgentCareTopic.pet_id == pet.id).order_by(AgentCareTopic.id)).scalars().all()
        for topic in topics:
            if topic.kind == 'device' and topic.status in ('open', 'ongoing'):
                if _project_topic(session, pet, topic, backfilled=True, now=now):
                    created += 1
        state.care_watermark = max((t.id for t in topics), default=0)
        last = session.execute(select(AgentTaskDelivery.id).join(
            AgentTask, AgentTask.id == AgentTaskDelivery.task_id)
            .where(AgentTask.pet_id == pet.id)
            .order_by(AgentTaskDelivery.id.desc()).limit(1)).scalars().first()
        state.delivery_watermark = last or 0
        state.updated_at = now
    else:
        topics = session.execute(select(AgentCareTopic).where(
            AgentCareTopic.pet_id == pet.id, AgentCareTopic.id > state.care_watermark)
            .order_by(AgentCareTopic.id).limit(SCAN_LIMIT)).scalars().all()
        for topic in topics:
            if _project_topic(session, pet, topic, now=now):
                created += 1
            state.care_watermark = topic.id
        # 仍开放的设备主题：严重度升级形成新条目（键含严重度）
        open_device = session.execute(select(AgentCareTopic).where(
            AgentCareTopic.pet_id == pet.id, AgentCareTopic.kind == 'device',
            AgentCareTopic.status.in_(('open', 'ongoing'))
        ).order_by(AgentCareTopic.id).limit(SCAN_LIMIT)).scalars().all()
        for topic in open_device:
            if _project_topic(session, pet, topic, now=now):
                created += 1
        # 到期回信：按本宠的交付定向扫描（有界，不用全量载入）
        deliveries = session.execute(select(AgentTaskDelivery).join(
            AgentTask, AgentTask.id == AgentTaskDelivery.task_id)
            .where(AgentTask.pet_id == pet.id,
                   AgentTaskDelivery.id > state.delivery_watermark)
            .order_by(AgentTaskDelivery.id).limit(SCAN_LIMIT)).scalars().all()
        for delivery in deliveries:
            if _project_delivery(session, pet, delivery, now=now):
                created += 1
            state.delivery_watermark = delivery.id
        state.updated_at = now
    # 富化：发布可能晚于水位推进，重新解析精确回合身份并刷新判定。
    # 取**最新**的一批：迟到的发布总是落在新条目上，条目累计超过一批后最旧的不再刷新。
    rows = session.execute(select(AgentInboxItem).where(
        AgentInboxItem.pet_id == pet.id).order_by(AgentInboxItem.id.desc())
        .limit(ENRICH_LIMIT)).scalars().all()
    for row in rows:
        _enrich_item(session, row, now=now)
    return created


def project_user(session, user_id, now=None):
    """投影一个用户的全部宠物（读路径调用，保证「创建后最终可见」）。"""
    now = now or utc_now()
    pets = session.execute(select(Pet).where(Pet.user_id == user_id)).scalars().all()
    return sum(project_pet(session, pet, now=now) for pet in pets)


def project_all(session, now=None):
    """投影全部宠物（后台周期作业用）。"""
    now = now or utc_now()
    pets = session.execute(select(Pet)).scalars().all()
    return sum(project_pet(session, pet, now=now) for pet in pets)


# ── 读取 ──────────────────────────────────────────────────────────────────

def _items_query(session, user_id, pet_id=None, before=None, limit=PAGE_LIMIT):
    stmt = select(AgentInboxItem).where(AgentInboxItem.user_id == user_id)
    if pet_id is not None:
        stmt = stmt.where(AgentInboxItem.pet_id == pet_id)
    if before:
        stmt = stmt.where(AgentInboxItem.id < before)
    return stmt.order_by(AgentInboxItem.id.desc()).limit(limit)


def list_items(session, user_id, pet_id=None, before=None, limit=PAGE_LIMIT):
    """收件箱列表（稳定游标：id 倒序；多取一条用于判断是否还有下一页）。"""
    limit = max(1, min(int(limit or PAGE_LIMIT), MAX_PAGE_LIMIT))
    rows = session.execute(_items_query(session, user_id, pet_id, before, limit + 1)).scalars().all()
    has_more = len(rows) > limit
    return rows[:limit], has_more


def unread_count(session, user_id, pet_id=None):
    stmt = select(AgentInboxItem.id).where(AgentInboxItem.user_id == user_id,
                                           AgentInboxItem.read_at.is_(None))
    if pet_id is not None:
        stmt = stmt.where(AgentInboxItem.pet_id == pet_id)
    return len(session.execute(stmt).scalars().all())


def _live_policy(session, item):
    """按来源**当前**状态算策略输入（设备关闭后退出关键优先级）。"""
    ref = json.loads(item.source_ref or '{}')
    severity, source_status = '', ''
    if item.source_kind == SOURCE_REPLY:
        delivery = session.get(AgentTaskDelivery, ref.get('deliveryId'))
        source_status = 'delivered' if delivery is not None else ''
    else:
        topic = session.get(AgentCareTopic, ref.get('topicId'))
        if topic is not None:
            severity, source_status = topic.severity, topic.status
    return policy_input(_eligibility_of(item), severity=severity, source_status=source_status)


def preview_item(session, user_id, pet_id=None):
    """今日预览：最多一条未读（D2）。优先级按中心策略，同优先级取最新。"""
    stmt = select(AgentInboxItem).where(AgentInboxItem.user_id == user_id,
                                        AgentInboxItem.read_at.is_(None))
    if pet_id is not None:
        stmt = stmt.where(AgentInboxItem.pet_id == pet_id)
    rows = session.execute(stmt.order_by(AgentInboxItem.id.desc()).limit(ENRICH_LIMIT)).scalars().all()
    best = None
    for row in rows:
        tier = preview_tier(_live_policy(session, row))
        if tier is None:
            continue
        if best is None or tier < best[0]:
            best = (tier, row)
    return best[1] if best else None


def visible_watermark(session, user_id, pet_id=None):
    """请求生效时可见的最大条目 id（全部已读的水位）。"""
    stmt = select(AgentInboxItem.id).where(AgentInboxItem.user_id == user_id)
    if pet_id is not None:
        stmt = stmt.where(AgentInboxItem.pet_id == pet_id)
    row = session.execute(stmt.order_by(AgentInboxItem.id.desc()).limit(1)).scalar_one_or_none()
    return row or 0


# ── 已读 ──────────────────────────────────────────────────────────────────

def mark_read(session, user_id, item_id, now=None):
    """单条已读（幂等）。已读不解除告警：来源状态各自持久化。"""
    now = now or utc_now()
    row = session.get(AgentInboxItem, item_id)
    if row is None or row.user_id != user_id:
        return None
    if row.read_at is None:
        row.read_at = now
        row.updated_at = now
    return row


def mark_all_read(session, user_id, pet_id=None, through_id=None, now=None):
    """全部已读：只处理请求生效时可见水位内的条目（并发新建不被一并处理）。"""
    now = now or utc_now()
    limit = through_id if through_id is not None else visible_watermark(session, user_id, pet_id)
    stmt = select(AgentInboxItem).where(AgentInboxItem.user_id == user_id,
                                         AgentInboxItem.read_at.is_(None),
                                         AgentInboxItem.id <= limit)
    if pet_id is not None:
        stmt = stmt.where(AgentInboxItem.pet_id == pet_id)
    rows = session.execute(stmt).scalars().all()
    for row in rows:
        row.read_at = now
        row.updated_at = now
    return len(rows), limit


def mark_personal_eligibility(session, user_id, item_id, *, relevance=None,
                               frozen_evidence=None, delivery_approved=None, now=None):
    """W11 合并入口：写入「冻结证据 + 明确相关性 + W09 交付批准」三项输入。

    W12 不解释这三项的含义，只按中心策略重算高相关标记与推送候选；
    缺失相关性不得按 high 处理（缺失即不成立）。
    """
    now = now or utc_now()
    row = session.get(AgentInboxItem, item_id)
    if row is None or row.user_id != user_id:
        return None
    current = _eligibility_of(row)
    updated = current.with_personal(relevance=relevance,
                                    frozen_evidence=frozen_evidence,
                                    delivery_approved=delivery_approved)
    _store_eligibility(session, row, updated, now=now)
    _maybe_publish_push(session, row, was_candidate=current.push_candidate)
    return row


def push_candidates(session, user_id, pet_id=None):
    """W13 输入：推送候选条目（中心策略判定）。"""
    stmt = select(AgentInboxItem).where(AgentInboxItem.user_id == user_id)
    if pet_id is not None:
        stmt = stmt.where(AgentInboxItem.pet_id == pet_id)
    rows = session.execute(stmt.order_by(AgentInboxItem.id.desc()).limit(ENRICH_LIMIT)).scalars().all()
    return [row for row in rows if push_candidate(_live_policy(session, row))]


# ── 深链解析 ──────────────────────────────────────────────────────────────

def resolve_target(session, item):
    """按稳定收件箱 ID 解析原目标。返回路由、参数与**可定位锚点**。

    锚点（anchor）= 交付回合 id（turnId）；客户端据此在会话里定位到那条来源消息，
    而不是停在占位路由。
    """
    ref = json.loads(item.source_ref or '{}')
    params = {'inboxId': str(item.id)}
    fields, detail = hydrate(session, item)
    if not fields['available']:
        reason = detail.get('reason') or 'source_unavailable'
        return {'kind': 'unknown', 'route': '/agent-inbox', 'params': params,
                'available': False, 'reason': reason, 'anchor': None}
    if item.source_kind in (SOURCE_DEVICE_FACT, SOURCE_CARE, SOURCE_LEARNING):
        topic = session.get(AgentCareTopic, ref.get('topicId'))
        params['topicId'] = str(topic.id)
        if item.turn_id:
            params['turnId'] = str(item.turn_id)
        return {'kind': 'care_topic', 'route': '/agent', 'params': params,
                'available': True, 'reason': '', 'anchor': item.turn_id}
    if item.source_kind == SOURCE_REPLY:
        delivery = session.get(AgentTaskDelivery, ref.get('deliveryId'))
        params['deliveryId'] = str(delivery.id)
        params['taskId'] = str(delivery.task_id)
        if delivery.turn_id:
            params['turnId'] = str(delivery.turn_id)
        return {'kind': 'task_reply', 'route': '/agent', 'params': params,
                'available': True, 'reason': '', 'anchor': delivery.turn_id}
    return {'kind': 'unknown', 'route': '/agent-inbox', 'params': params,
            'available': False, 'reason': 'unknown_source', 'anchor': None}


def source_detail(session, item):
    """来源正文与证据（读时水合；来源不可访问时给出明确的安全历史状态）。"""
    _fields, detail = hydrate(session, item)
    return detail


# ── 序列化 ────────────────────────────────────────────────────────────────

def public(item, *, source=None, target=None, fields=None):
    """条目公开序列化（列表/详情共用；标题与摘要来自来源，不落索引表）。"""
    eligibility = _eligibility_of(item)
    fields = fields or {}
    payload = {'id': item.id, 'petId': item.pet_id, 'sourceKind': item.source_kind,
               'eventKey': item.event_key, 'title': fields.get('title', ''),
               'summary': fields.get('summary', ''),
               'severity': fields.get('severity', ''),
               'sourceStatus': fields.get('sourceStatus', ''),
               'available': bool(fields.get('available', True)),
               'priority': item.priority, 'previewable': bool(item.previewable),
               'published': bool(item.published), 'turnId': item.turn_id,
               'highRelevance': bool(item.high_relevance),
               'pushCandidate': bool(item.push_candidate),
               'status': item.status, 'backfilled': bool(item.backfilled),
               'readAt': iso_z(item.read_at) if item.read_at else None,
               'createdAt': iso_z(item.created_at), 'updatedAt': iso_z(item.updated_at),
               'sourceRef': json.loads(item.source_ref or '{}'),
               'eligibility': eligibility.as_dict()}
    if source is not None:
        payload['source'] = source
    if target is not None:
        payload['target'] = target
    return payload


def turn_public(turn):
    """回合公开序列化（与 agent/routes.public 同形状）：深链按锚点取回**那一条**来源消息。

    用于「锚点回合不在当前会话已加载的那一页」的场景：客户端凭稳定回合 id 精确取回，
    不依赖会话分页。
    """
    return {'id': turn.id, 'requestId': turn.request_id, 'text': turn.text,
            'topicId': turn.topic_id, 'replyTo': turn.reply_to,
            'sources': json.loads(turn.sources), 'speaker': turn.speaker,
            'readAt': iso_z(turn.read_at) if turn.read_at else None,
            'target': turn.target, 'reply': turn.reply, 'status': turn.status,
            'blocks': json.loads(turn.blocks) if turn.blocks else None,
            'images': json.loads(turn.images) if turn.images else [],
            'createdAt': iso_z(turn.created_at)}


def find_turn(session, user_id, turn_id):
    """按回合 id 取回本人名下宠物的一条回合；不可访问时返回 None。"""
    turn = session.get(AgentTurn, turn_id)
    if turn is None:
        return None
    pet = session.get(Pet, turn.pet_id)
    if pet is None or pet.user_id != user_id:
        return None
    return turn


def capability(user_id=None):
    """服务端能力声明（客户端据此接管计数与已读语义）。"""
    from petsense.agent import care
    return {'capability': 'agent_inbox', 'version': 2,
            'serverInbox': True,
            'managedRules': list(care.MANAGED_RULES),
            'sourceKinds': list(SOURCE_KINDS)}


def public_list(session, rows):
    """列表序列化：逐条水合来源（正文不落索引表）。"""
    items = []
    for row in rows:
        fields, _detail = hydrate(session, row)
        items.append(public(row, fields=fields))
    return items


__all__ = ['project_pet', 'project_user', 'project_source', 'project_all', 'list_items',
           'public_list', 'unread_count', 'preview_item', 'mark_read', 'mark_all_read',
           'mark_personal_eligibility', 'push_candidates', 'resolve_target', 'source_detail',
           'public', 'capability', 'visible_watermark', 'hydrate', 'push_facts',
           'install_push_bridge', 'PAGE_LIMIT']
