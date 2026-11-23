"""W09 主动关怀作业执行器（agent-w09-spec.md §2/§3）：scan / review / event 三种 kind。

全部注册到 W08 持久作业队列（jobs.register_handler），复用其去重、租约、退避与
重启恢复——本包不自建调度。scan 周期自排（返回 next_run_at 保持 pending），负责：
宠物启用水位、周期回顾窗口入队、失联扫描与学习完成转换；review/event 为一次性
作业：组装确定性事实 → loop.run_background（统一循环的后台形态）→ 服务端校验
（主体、窗口、主题引用、交付预算）→ 按助手设定交付（deliver_background_message
稳定 request key 去重，提交后推 WS 帧）。模型不可用或输出不合法时按事实降级
（W04 facts-only 同语义），状态如实入轨迹。
"""
import json
import logging
from datetime import timedelta

from sqlalchemy import select

from petsense.agent import care, deliver, jobs, loop, registry, runs
from petsense.agent import settings as settings_service
from petsense.agent.observations import query as observations_query
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Pet
from petsense.db.models.agent import AgentCareTopic, CareDeviceState

logger = logging.getLogger(__name__)

SCAN_KIND = 'care_scan'
REVIEW_KIND = 'care_review'
EVENT_KIND = 'care_event'

FINDING_BUDGET = 3          # 每次回顾的发现交付预算（服务端截断，spec §2）
EVIDENCE_DAYS_SHOWN = 7

_REVIEW_SYSTEM = (
    '任务：为一只宠物生成周期回顾（昨日或上一自然周）。判断什么值得告诉主人：'
    '只依据给定窗口统计、覆盖与缺口、学习进度、近期主题和工具查询结果；'
    '覆盖不足的时间段如实说明，不推断为异常、活动减少或健康问题；'
    '按主人设置的主动程度把握分寸。'
    '输出 JSON：{"say": "给主人看的回顾消息（没事可说时为空字符串）", '
    '"findings": [{"category": "walk_volume|sound_events|coverage|observation|other", '
    '"anchor": "同一事件的自然键（小写英文与数字，跨天指同一件事时保持一致）", '
    '"title": "短标题", "text": "这条发现的一句说明"}]}。'
    'findings 为空数组表示零发现；已在「近期主题」里报告过的事不要作为新发现重复提报，'
    '确有新进展时沿用同一 anchor。say 中不要罗列全部数字，挑值得说的说。')

_EVENT_SYSTEM = (
    '任务：就一条已确认的设备/学习事实写主动提醒。事实与依据由系统给出，不可更改；'
    '你决定表达、相关性，以及是否值得此刻打扰主人（disturb）。'
    '低电/失联/读数无效是关键设备事实，通常值得提醒；轻微或已快恢复的可以建议不打扰。'
    '语气自然简短，说明依据时间（如「刚才」「今早」），不使用原始字段名。'
    '输出 JSON：{"say": "给主人的提醒文案", "disturb": true|false}。')


def _run_background_form(pet_id, user_id, form, system_extra, task, trigger, settings):
    """统一循环的后台形态往返；异常向上抛由作业层退避重试。"""
    context = {'pet_id': pet_id, 'user_id': user_id, 'form': form, 'task': task,
               'system_extra': system_extra, 'trigger': trigger, 'settings': settings}
    return loop.run_background(context)


def _parse_task_json(text):
    """容错解析模型任务 JSON（允许前后缀少量文字/代码块围栏）。返回 dict 或 None。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`').lstrip('json').strip()
    try:
        value = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find('{'), cleaned.rfind('}')
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(cleaned[start:end + 1])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


# ── scan：周期自排的调度扫描 ────────────────────────────────────────────────

def handle_scan(job, now):
    """一轮调度扫描：水位、回顾窗口、失联与学习转换。返回下一轮时间保持 pending。"""
    with session_scope() as s:
        pets = s.execute(select(Pet)).scalars().all()
        for pet in pets:
            care.ensure_pet_state(s, pet, now)
            if not care.device_bound_pet(s, pet):
                continue
            for plan in care.due_reviews(s, pet, now):
                jobs.enqueue(s, REVIEW_KIND,
                             {'pet': pet.id, 'kind': plan['kind'], 'key': plan['key'],
                              'windowStart': iso_z(plan['windowStart']),
                              'windowEnd': iso_z(plan['windowEnd']),
                              'scheduledAt': iso_z(plan['scheduledAt'])},
                             dedup_key='care:review:%s:%d:%s' % (plan['kind'], pet.id, plan['key']),
                             next_run_at=now)
            for item in care.learning_events(s, pet, now):
                jobs.enqueue(s, EVENT_KIND, item,
                             dedup_key='care:event:topic:%d:%s' % (item['topic'], item['phase']),
                             next_run_at=now)
        for item in care.detect_stale(s, now):
            jobs.enqueue(s, EVENT_KIND, item,
                         dedup_key='care:event:topic:%d:%s' % (item['topic'], item['phase']),
                         next_run_at=now)
    return {'next_run_at': now + timedelta(seconds=care.SCAN_INTERVAL_S)}


# ── review：周期回顾运行 ────────────────────────────────────────────────────

def _parse_iso(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def review_facts(session, pet, window_start, window_end):
    """回顾事实组装：逐日统计/覆盖/缺口 + 近期开放主题（统一观测口径；无数据如实）。"""
    tz = care.pet_timezone(pet)
    days = []
    day = window_start.astimezone(tz).date()
    last_day = (window_end - timedelta(microseconds=1)).astimezone(tz).date()
    while day <= last_day:
        bounds = care.day_bounds(day.isoformat(), tz)
        summary = observations_query(session, pet, bounds[0], bounds[1])
        days.append({'day': day.isoformat(),
                     'observedMinutes': int(summary.get('observedSeconds', 0) // 60),
                     'walkMinutes': int((summary.get('actionSeconds') or {}).get('走动', 0) // 60),
                     'soundEventCount': summary.get('soundEventCount', 0),
                     'minuteCount': summary.get('minuteCount', 0)})
        day += timedelta(days=1)
    covered = [d for d in days if d['minuteCount'] > 0]
    topics = session.execute(select(AgentCareTopic).where(
        AgentCareTopic.pet_id == pet.id,
        AgentCareTopic.status.in_(('open', 'ongoing', 'recovered')))
        .order_by(AgentCareTopic.updated_at.desc()).limit(5)).scalars().all()
    return {'window': {'startAt': iso_z(window_start), 'endAt': iso_z(window_end)},
            'days': days, 'coveredDays': len(covered), 'totalDays': len(days),
            'openTopics': [{'id': t.id, 'category': t.category, 'title': t.title,
                            'status': t.status, 'anchor': t.anchor,
                            'updatedAt': iso_z(t.updated_at)} for t in topics]}


def _review_fallback_text(facts):
    """模型不可用/输出不合法时的事实降级文案（只报真实统计，零推断）。"""
    covered = facts['coveredDays']
    total = facts['totalDays']
    walk = sum(d['walkMinutes'] for d in facts['days'])
    sounds = sum(d['soundEventCount'] for d in facts['days'])
    if not covered:
        return '这段时间项圈没有留下观测记录，回顾先欠着，有数据后再说。'
    lines = ['这段时间的观察小结：']
    for d in facts['days']:
        if d['minuteCount'] == 0:
            lines.append('%s：没有观测记录' % d['day'])
        else:
            lines.append('%s：观测约 %d 分钟，走动 %d 分钟，声音事件 %d 次'
                         % (d['day'], d['observedMinutes'], d['walkMinutes'], d['soundEventCount']))
    lines.append('（共 %d/%d 天有观测，走动合计 %d 分钟，声音事件合计 %d 次）'
                 % (covered, total, walk, sounds))
    return '\n'.join(lines)


def _validate_findings(payload, pet_id, session):
    """校验模型发现：类别白名单、锚点归一、主题引用同主体、锚点去重与交付预算。

    返回 (findings, merged_anchors)：merged 为已被既有已交付主题承接（沿用同一
    anchor 的开放主题）而不重复提报的锚点。
    """
    raw = payload.get('findings') if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return [], []
    findings, anchors, merged = [], [], []
    for item in raw:
        if not isinstance(item, dict):
            continue
        category = item.get('category') if item.get('category') in care.REVIEW_CATEGORIES else 'other'
        anchor = str(item.get('anchor') or '').strip().lower()[:120]
        text = str(item.get('text') or '').strip()[:300]
        if not anchor or not text:
            continue
        if anchor in anchors:
            continue
        anchors.append(anchor)
        existing = session.execute(select(AgentCareTopic).where(
            AgentCareTopic.pet_id == pet_id, AgentCareTopic.kind == 'observation',
            AgentCareTopic.anchor == anchor,
            AgentCareTopic.status.in_(('open', 'ongoing')))).scalar_one_or_none()
        if existing is not None:
            merged.append(anchor)
            care.append_evidence(existing, {'at': iso_z(utc_now()), 'kind': 'review',
                                            'note': text[:120]})
            continue
        findings.append({'category': category, 'anchor': anchor,
                         'title': str(item.get('title') or category)[:80], 'text': text})
        if len(findings) >= FINDING_BUDGET:
            break
    return findings, merged


def handle_review(job, now):
    payload = job['payload']
    with session_scope() as s:
        pet = s.get(Pet, payload['pet'])
        if pet is None or not care.device_bound_pet(s, pet):
            return  # 归属或绑定变化后不再回顾（窗口作业自然完成）
        settings = settings_service.load(s, pet.user_id)
        facts = review_facts(s, pet,
                             _parse_iso(payload['windowStart']), _parse_iso(payload['windowEnd']))
    task = ('宠物：%s（%s）\n窗口：%s 至 %s\n回顾事实（JSON）：\n%s\n'
            '主人主动程度：%s（0=安静不发主动消息，1=标准只说高相关，2=多提醒可含非紧急但有依据的发现）\n'
            '请按任务说明输出 JSON。'
            % (pet.name, pet.species or '', payload['windowStart'], payload['windowEnd'],
               json.dumps(facts, ensure_ascii=False), settings.get('proactivity', 1)))
    trigger = {'kind': payload['kind'], 'window': payload['key'], 'petId': pet.id}
    degraded, say, findings, merged = [], '', [], []
    trace = {'form': registry.REVIEW, 'trigger': trigger, 'messages': [{'task': task[:600]}],
             'tool_summary': [], 'budget': {}, 'degraded': []}
    try:
        outcome = _run_background_form(pet.id, pet.user_id, registry.REVIEW,
                                       _REVIEW_SYSTEM, task, trigger, settings)
        trace = outcome['trace']
        payload_json = _parse_task_json(outcome['text'])
        degraded = list(outcome['degraded'])
        if payload_json is None:
            degraded.append('task_json_invalid')
            say = _review_fallback_text(facts)
        else:
            say = str(payload_json.get('say') or '').strip()
            with session_scope() as s:
                findings, merged = _validate_findings(payload_json, pet.id, s)
    except Exception:
        logger.exception('care review model run failed for pet %s', pet.id)
        degraded.append('model_unavailable')
        say = _review_fallback_text(facts)
    status = 'degraded' if degraded else 'complete'
    trace['trigger'] = trigger
    trace['degraded'] = degraded
    trace['conclusion'] = {'reason_code': 'degraded:' + ','.join(degraded) if degraded else 'ok',
                           'window': payload['key'], 'findings': len(findings), 'mergedAnchors': merged}
    trace['snapshot'] = {'reviewFacts': facts}
    run_id = runs.save(pet.id, None, trace, status)
    delivered = None
    with session_scope() as s:
        for finding in findings:
            care.open_topic(s, pet.id, 'observation', finding['category'], finding['anchor'],
                            severity='info', title=finding['title'], detail=finding['text'],
                            evidence=[{'at': iso_z(now), 'kind': 'review', 'note': finding['text'][:120],
                                       'window': payload['key']}], now=now)
        if settings.get('proactivity', 1) > 0 and say:
            blocks = [{'type': 'text', 'speaker': 'assistant', 'text': say},
                      {'type': 'care', 'care': {'kind': 'review', 'category': payload['kind'],
                                                'title': '周期回顾', 'status': 'info',
                                                'windowStart': payload['windowStart'],
                                                'windowEnd': payload['windowEnd'],
                                                'runId': run_id}}]
            turn, created = deliver.deliver_background_message(
                s, pet.id, 'care:review:%s:%d:%s' % (payload['kind'], pet.id, payload['key']),
                blocks, sources=[], now=now)
            if created:
                delivered = (pet.user_id, turn)
    if delivered:
        deliver.push_delivered(delivered[0], delivered[1].pet_id, delivered[1])  # push_delivered 内部做 public()
    return None


# ── event：硬件/学习事件运行 ────────────────────────────────────────────────

def handle_event(job, now):
    payload = job['payload']
    with session_scope() as s:
        topic = s.get(AgentCareTopic, payload.get('topic'))
        if topic is None or topic.status in ('recovered', 'closed'):
            return  # 作业执行前已恢复/关闭：首发消息不再必要（恢复不发消息）
        pet = s.get(Pet, topic.pet_id)
        if pet is None:
            return
        settings = settings_service.load(s, pet.user_id)
        state_row = s.get(CareDeviceState, topic.anchor) if topic.kind == 'device' else None
        facts = care.event_facts(topic, state_row)
    task = ('宠物：%s\n事件事实（JSON）：\n%s\n阶段：%s\n主人主动程度：%s\n请按任务说明输出 JSON。'
            % (pet.name, json.dumps(facts, ensure_ascii=False), payload.get('phase'),
               settings.get('proactivity', 1)))
    trigger = {'kind': 'event', 'category': topic.category, 'phase': payload.get('phase'),
               'topicId': topic.id, 'petId': pet.id}
    degraded, say, disturb = [], '', True
    trace = {'form': registry.EVENT, 'trigger': trigger, 'messages': [{'task': task[:600]}],
             'tool_summary': [], 'budget': {}, 'degraded': []}
    try:
        outcome = _run_background_form(pet.id, pet.user_id, registry.EVENT, _EVENT_SYSTEM, task,
                                       trigger, settings)
        trace = outcome['trace']
        degraded = list(outcome['degraded'])
        payload_json = _parse_task_json(outcome['text'])
        if payload_json is None:
            degraded.append('task_json_invalid')
            say = care.deterministic_event_text(topic, payload.get('phase'))
        else:
            say = str(payload_json.get('say') or '').strip() or \
                care.deterministic_event_text(topic, payload.get('phase'))
            disturb = payload_json.get('disturb')
            if not isinstance(disturb, bool):
                disturb = True
    except Exception:
        logger.exception('care event model run failed for topic %s', topic.id)
        degraded.append('model_unavailable')
        say = care.deterministic_event_text(topic, payload.get('phase'))
    status = 'degraded' if degraded else 'complete'
    trace['trigger'] = trigger
    trace['degraded'] = degraded
    trace['conclusion'] = {'reason_code': 'degraded:' + ','.join(degraded) if degraded else 'ok',
                           'disturb': disturb, 'delivered': bool(say)}
    trace['snapshot'] = {'eventFacts': facts}
    run_id = runs.save(pet.id, None, trace, status)
    # 交付决策归代码：安静档一律不发；标准/多提醒尊重模型的「不值得打扰」，降级时事实关键默认发
    proactivity = settings.get('proactivity', 1)
    deliverable = bool(say) and proactivity > 0 and disturb is not False
    delivered_turn = None
    with session_scope() as s:
        topic = s.get(AgentCareTopic, payload.get('topic'))
        if topic is not None:
            topic.run_id = topic.run_id or run_id
        if deliverable:
            blocks = [{'type': 'text', 'speaker': 'assistant', 'text': say},
                      {'type': 'care', 'care': {'kind': topic.kind if topic else 'device',
                                                'category': topic.category if topic else '',
                                                'title': topic.title if topic else '',
                                                'status': topic.status if topic else 'open',
                                                'severity': topic.severity if topic else 'warning',
                                                'topicId': payload.get('topic'),
                                                'openedAt': facts['openedAt'],
                                                'evidence': facts['evidence'][-5:],
                                                'runId': run_id}}]
            turn, created = deliver.deliver_background_message(
                s, pet.id, 'care:event:%d:%s' % (payload.get('topic'), payload.get('phase')),
                blocks, topic_id=payload.get('topic'), sources=[], now=now)
            if created:
                if topic is not None:
                    topic.delivered += 1
                s.flush()
                delivered_turn = (pet.user_id, turn)
    if delivered_turn:
        deliver.push_delivered(delivered_turn[0], delivered_turn[1].pet_id, delivered_turn[1])
    return None


def register():
    """注册三种 kind 执行器（app 启动时调用；重复注册覆盖，测试隔离用）。"""
    jobs.register_handler(SCAN_KIND, handle_scan)
    jobs.register_handler(REVIEW_KIND, handle_review)
    jobs.register_handler(EVENT_KIND, handle_event)


def start_care():
    """注册执行器并确保 scan 周期作业存在（幂等；PETSENSE_SCHEDULER_DISABLED 时仅注册不排队）。"""
    register()
    if not jobs.scheduler_enabled():
        return
    with session_scope() as s:
        jobs.enqueue(s, SCAN_KIND, {}, dedup_key='care:scan:loop',
                     next_run_at=utc_now() + timedelta(seconds=10))
