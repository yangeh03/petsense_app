"""W08 委托域：计划归一化、状态机、证据物化、检查点调度与交付（agent-w08-spec.md §2–§3）。

职责切分（判断归模型、事实与执行归代码）：本模块只做确定性的计划校验、状态转换、
窗口数据物化、游标与去重；检查点模型运行在 agent/checkpoint.py。确认门复用 W03
AgentProposal（kind=task_change），确认/拒绝经 proposals.decide 分派到本模块的
apply_proposal；委托簿内暂停/恢复/取消直接生效（D3）。交付走 agent/deliver.py 的
后台回合边界（稳定 request key + WS 推帧 + HTTP 补读收敛）。

时间语义（P00 §1 / spec §2）：范围左闭右开；绝对时间带时区落库；自然窗口按计划时区
解释（缺省 Asia/Shanghai）。deadline 不因暂停延长；暂停窗口在证据里计为未观察缺口；
确认晚于原定开始时以 effective_from 截断（先前窗口不算已观察）；wait_baseline 委托
只计 observing_since 之后的覆盖。
"""
import json
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from petsense.agent import jobs
from petsense.agent.baseline import dimension_states
from petsense.agent.observations import query as observations_query
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Pet, TelemetryMinute
from petsense.db.models.agent import (AgentTask, AgentTaskDelivery, AgentTaskPlan, AgentTurn)

# 三类计划（spec §2）；「待确认」为 AgentProposal(pending) 态，不在本表状态集内
KIND_FACT_WATCH = 'fact_watch'
KIND_COMPARISON = 'comparison'
KIND_REVIEW = 'scheduled_review'
PLAN_KINDS = (KIND_FACT_WATCH, KIND_COMPARISON, KIND_REVIEW)
KIND_LABELS = {KIND_FACT_WATCH: '事实观察', KIND_COMPARISON: '个人比较', KIND_REVIEW: '定时回顾'}
STATUS_LABELS = {'waiting_baseline': '等待基线', 'observing': '观察中', 'paused': '已暂停',
                 'completed': '已交付', 'cancelled': '已取消'}
FEATURE_LABELS = {'actions': '走动', 'sounds': '声音事件'}
METRIC_OF_FEATURE = {'actions': 'walkSeconds', 'sounds': 'soundEventCount'}
WALK_LABEL = '走动'

DEFAULT_TZ = 'Asia/Shanghai'
DEFAULT_DEADLINE_DAYS = 3     # D1：未给期限默认 3 天
MAX_SPAN_DAYS = 30            # D1：单版本最长 30 天
MAX_QUESTION_CHARS = 200
MAX_TITLE_CHARS = 24

ORDINARY_MIN_INTERVAL = timedelta(hours=1)  # 普通模型检查每委托至多每小时一次（D5）
MAX_DUE_RETRIES = 1           # D5/D6：到期失败重试一次，再失败交付降级回信
DUE_RETRY_DELAY = timedelta(seconds=60)
PAUSED_POLL = timedelta(minutes=15)
WAIT_BASELINE_POLL = timedelta(minutes=15)

CHECKPOINT_KIND = 'task.checkpoint'

# 交付事务提交后待推的 WS 帧（事务内不推 WS；handle_checkpoint_job 出事务后统一 flush）
_PENDING_PUSHES = []


class TaskError(Exception):
    """结构化错误：status 为 HTTP 状态码，message 面向客户端/模型如实转述。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── 计划归一化 ──────────────────────────────────────────────────────────────

def _parse_time(value, tz):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def iso_z_parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def normalize_plan(fields, now=None):
    """计划字段归一化与结构校验（落库前复核，P00 §4）。

    输入为模型/客户端结构化提案（camelCase）；不通过抛 TaskError（字段级原因并入消息）。
    通过返回归一化计划 payload（camelCase，确认卡与评估共用）。
    """
    if not isinstance(fields, dict):
        raise TaskError('plan 需为对象')
    errors = []
    now = now or utc_now()
    kind = fields.get('kind')
    if kind not in PLAN_KINDS:
        raise TaskError('kind 需为 fact_watch / comparison / scheduled_review 之一')
    tz_name = fields.get('timezone') or DEFAULT_TZ
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise TaskError(f'无法识别的时区 {tz_name}')
    feature = fields.get('feature')
    if kind in (KIND_FACT_WATCH, KIND_COMPARISON):
        if feature not in METRIC_OF_FEATURE:
            errors.append(f'{KIND_LABELS[kind]}计划需指定 feature（actions/sounds）')
    else:
        feature = feature if feature in METRIC_OF_FEATURE else None  # 回顾可选

    question = fields.get('question')
    if not isinstance(question, str) or not 3 <= len(question.strip()) <= MAX_QUESTION_CHARS:
        errors.append(f'question 需为 {MAX_QUESTION_CHARS} 字以内的问题描述')
    else:
        question = question.strip()

    condition = fields.get('condition')
    if condition is not None:
        if not isinstance(condition, dict) or kind == KIND_REVIEW:
            errors.append('condition 需为对象，且只有观察/比较计划可带阈值条件')
            condition = None
        else:
            fixed = dict(condition) if isinstance(condition, dict) else {}
            metric = fixed.get('metric')
            if feature is not None and metric != METRIC_OF_FEATURE[feature]:
                errors.append(f'条件指标 {metric} 与关注维度 {feature} 不匹配')
            if metric not in METRIC_OF_FEATURE.values():
                errors.append('metric 需为 walkSeconds 或 soundEventCount')
            if fixed.get('op') not in ('at_least', 'at_most'):
                errors.append('op 需为 at_least / at_most')
            value = fixed.get('value')
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                errors.append('value 需为非负数值')
            elif metric == 'soundEventCount' and isinstance(value, float):
                fixed['value'] = int(value)
            if fixed.get('scope') not in ('window', 'daily'):
                errors.append('scope 需为 window（自开始累计）或 daily（按日）')
            condition = None if errors else fixed

    comparison = None
    baseline_strategy = None
    if kind == KIND_COMPARISON:
        raw = fields.get('comparison')
        if not isinstance(raw, dict) or raw.get('band') not in ('below', 'above'):
            errors.append('个人比较需指定 comparison.band（below=低于平时 / above=高于平时）')
        else:
            comparison = {'band': raw['band']}
        baseline_strategy = fields.get('baselineStrategy') or 'fact_first'
        if baseline_strategy not in ('fact_first', 'wait_baseline'):
            errors.append('baselineStrategy 需为 fact_first / wait_baseline')

    start = _parse_time(fields.get('startAt'), tz)
    deadline = _parse_time(fields.get('deadlineAt'), tz) if fields.get('deadlineAt') else None
    if fields.get('startAt') and start is None:
        errors.append('startAt 不可解析（ISO 时间，缺时区按计划时区解释）')
        start = now
    if fields.get('deadlineAt') and deadline is None:
        errors.append('deadlineAt 不可解析（ISO 时间，缺时区按计划时区解释）')
    if start is None:
        start = now  # 缺省从现在开始
    if deadline is None:
        deadline = start + timedelta(days=DEFAULT_DEADLINE_DAYS)
    if deadline <= start:
        errors.append('截止时间需晚于开始时间')
    elif deadline - start > timedelta(days=MAX_SPAN_DAYS):
        errors.append(f'单版本委托最长 {MAX_SPAN_DAYS} 天')
    if deadline <= now:
        errors.append('截止时间需在未来')
    if errors:
        raise TaskError('；'.join(errors))

    title = (fields.get('title') if isinstance(fields.get('title'), str) else '').strip()
    if not title:
        title = (question or KIND_LABELS[kind])[:MAX_TITLE_CHARS]
    return {'kind': kind, 'title': title[:MAX_TITLE_CHARS], 'question': question,
            'feature': feature, 'condition': condition, 'comparison': comparison,
            'baselineStrategy': baseline_strategy, 'timezone': str(tz),
            'startAt': iso_z(start), 'deadlineAt': iso_z(deadline)}


def normalize_proposal_fields(fields, now=None):
    """propose_task_change 的提案负载归一化：action + plan（创建/修改/续期）或任务操作。"""
    if not isinstance(fields, dict):
        raise TaskError('提案需为对象')
    action = fields.get('action')
    if action not in ('create', 'update', 'renew', 'pause', 'resume', 'cancel'):
        raise TaskError('action 需为 create / update / renew / pause / resume / cancel')
    note = fields.get('note')
    payload = {'action': action, 'note': note[:300] if isinstance(note, str) else ''}
    if action in ('create', 'update', 'renew'):
        payload['plan'] = normalize_plan(fields.get('plan'), now=now)
    if action in ('update', 'renew', 'pause', 'resume', 'cancel'):
        task_id, version = _task_ref(fields)
        payload['taskId'] = task_id
        payload['expectedVersion'] = version
    return payload


def _task_ref(fields):
    task_id = fields.get('taskId')
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        raise TaskError('taskId 需为整数')
    version = fields.get('expectedVersion')
    if isinstance(version, bool) or not isinstance(version, int):
        raise TaskError('expectedVersion 需为整数（防止旧版本决策覆盖新状态）')
    return task_id, version


# ── 读取辅助 ────────────────────────────────────────────────────────────────

def plan_of(session, task):
    row = session.execute(select(AgentTaskPlan).where(
        AgentTaskPlan.task_id == task.id, AgentTaskPlan.version == task.current_version)
    ).scalar_one_or_none()
    if row is None:
        raise TaskError(f'任务 {task.id} 缺少版本 {task.current_version} 的计划', status=500)
    return json.loads(row.payload)


def _paused_intervals(task):
    result = []
    for item in json.loads(task.paused_intervals or '[]'):
        start = iso_z_parse(item['start'])
        end = iso_z_parse(item['end']) if item.get('end') else None
        result.append((start, end))
    return result


def _subtract_pauses(start, end, pauses):
    """[start, end) 减去暂停窗口（未关闭的暂停按 end 截断），返回有效观察子窗口列表。"""
    clipped = []
    for p_start, p_end in pauses:
        p_end = min(p_end, end) if p_end else end
        if max(p_start, start) < p_end:
            clipped.append((max(p_start, start), p_end))
    clipped.sort()
    windows, cursor = [], start
    for p_start, p_end in clipped:
        if p_start > cursor:
            windows.append((cursor, p_start))
        cursor = max(cursor, p_end)
    if cursor < end:
        windows.append((cursor, end))
    return windows


def observation_bounds(task, plan, end):
    """观察窗口边界：确认时间截断 + wait_baseline 只计进入观察之后（spec §2）。"""
    start = max(iso_z_parse(plan['startAt']), task.effective_from)
    if task.observing_since is not None:
        start = max(start, task.observing_since)
    return start, min(end, task.deadline_at)


def materialize(session, pet, task, plan, end):
    """物化观察证据（spec §3：输入包含物化观测、实际覆盖与缺口）。

    有效观察窗口 = [开始, end) ∩ 确认后 ∩（wait_baseline：进入观察后）− 暂停窗口；
    每个子窗口复用 observations.query，合并总量并按计划时区分日。缺口 = 有效秒数 −
    已观测秒数，只陈述未观测，不推断为正常。
    """
    start, end = observation_bounds(task, plan, end)
    tz = ZoneInfo(plan.get('timezone') or DEFAULT_TZ)
    evidence = {'windowStart': iso_z(start), 'windowEnd': iso_z(end),
                'effectiveWindows': [], 'pausedSeconds': 0, 'gapSeconds': 0,
                'observedSeconds': 0, 'walkSeconds': 0, 'soundEventCount': 0,
                'actionSeconds': {}, 'daily': [], 'dataThrough': None, 'baseline': None,
                'comparisonStarted': task.observing_since is not None}
    if end <= start:
        return evidence
    pauses = _paused_intervals(task)
    windows = _subtract_pauses(start, end, pauses)
    evidence['effectiveWindows'] = [f'{iso_z(a)}..{iso_z(b)}' for a, b in windows]
    effective_seconds = sum((b - a).total_seconds() for a, b in windows)
    evidence['pausedSeconds'] = int(max(0, (end - start).total_seconds() - effective_seconds))
    observed = sound_total = 0
    actions = {}
    for a, b in windows:
        if b <= a:
            continue
        try:
            result = observations_query(session, pet, a, b)
        except ValueError:
            continue
        observed += result.get('observedSeconds') or 0
        sound_total += result.get('soundEventCount') or 0
        for key, value in (result.get('actionSeconds') or {}).items():
            actions[key] = actions.get(key, 0) + value
    evidence['observedSeconds'] = int(observed)
    evidence['soundEventCount'] = int(sound_total)
    evidence['actionSeconds'] = actions
    evidence['walkSeconds'] = int(actions.get(WALK_LABEL, 0))
    evidence['gapSeconds'] = int(max(0, effective_seconds - observed))
    daily = []
    day_cursor = start.astimezone(tz).date()
    last_day = (end - timedelta(seconds=1)).astimezone(tz).date()
    while day_cursor <= last_day and len(daily) < 31:
        day_start = datetime.combine(day_cursor, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        day_end = day_start + timedelta(days=1)
        day_windows = _subtract_pauses(max(start, day_start), min(end, day_end), pauses)
        day_totals = {'walkSeconds': 0, 'soundEventCount': 0, 'observedSeconds': 0}
        for a, b in day_windows:
            if b <= a:
                continue
            try:
                result = observations_query(session, pet, a, b)
            except ValueError:
                continue
            day_totals['walkSeconds'] += (result.get('actionSeconds') or {}).get(WALK_LABEL, 0)
            day_totals['soundEventCount'] += result.get('soundEventCount') or 0
            day_totals['observedSeconds'] += result.get('observedSeconds') or 0
        daily.append({'day': day_cursor.isoformat(), **day_totals})
        day_cursor += timedelta(days=1)
    evidence['daily'] = daily
    if pet.device_id:
        data_through = session.execute(select(func.max(TelemetryMinute.minute)).where(
            TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
            TelemetryMinute.minute < end)).scalar()
        evidence['dataThrough'] = iso_z(data_through) if data_through else None
    if plan['kind'] == KIND_COMPARISON:
        from petsense.agent import baseline as baseline_service
        try:
            baseline = baseline_service.read_baseline(
                session, pet, features=[plan['feature']] if plan.get('feature') else None)
            evidence['baseline'] = {'date': baseline.get('date'), 'dimensions': baseline.get('dimensions')}
        except ValueError:
            evidence['baseline'] = {'error': 'baseline_unavailable'}
    return evidence


def condition_state(plan, evidence, now=None):
    """确定性条件核对（spec §3：代码用已物化证据校验结果是否可交付）。"""
    kind = plan['kind']
    condition = plan.get('condition')
    if kind == KIND_FACT_WATCH and condition:
        metric = condition['metric']
        key = 'walkSeconds' if metric == 'walkSeconds' else 'soundEventCount'
        if condition['scope'] == 'daily':
            tz = ZoneInfo(plan.get('timezone') or DEFAULT_TZ)
            today = (now or utc_now()).astimezone(tz).date().isoformat()
            today_row = next((d for d in evidence.get('daily', []) if d['day'] == today), None)
            value = (today_row or {}).get(key, 0)
        else:
            value = evidence.get(key, 0)
        met = value >= condition['value'] if condition['op'] == 'at_least' else value <= condition['value']
        return {'met': bool(met), 'value': value, 'metric': metric,
                'threshold': condition['value'], 'op': condition['op']}
    if kind == KIND_COMPARISON:
        dimensions = (evidence.get('baseline') or {}).get('dimensions') or []
        dim = next((d for d in dimensions if d.get('feature') == plan.get('feature')), None)
        if not dim:
            return {'met': False, 'reason': 'baseline_unavailable'}
        target = ((dim.get('day') or {}).get('target') or {})
        if not target.get('comparable'):
            return {'met': False, 'reason': 'not_comparable',
                    'note': target.get('note') or '当日覆盖不足或样本不足，暂不可比'}
        return {'met': target.get('band') == plan['comparison']['band'],
                'band': target.get('band'), 'wanted': plan['comparison']['band'],
                'value': target.get('value'), 'range': (dim.get('day') or {}).get('range'),
                'version': dim.get('version')}
    return {'met': False, 'reason': 'no_condition'}


def baseline_ready(session, pet, plan):
    if plan.get('feature') is None or pet is None:
        return False
    states = dimension_states(session, pet)
    row = next((s for s in states if s['dimension'] == plan['feature'] and s.get('active')), None)
    return bool(row and row['state'] == 'ready')


# ── 状态机：确认生效与委托簿直接操作 ─────────────────────────────────────────

def _topic_of(session, turn_id):
    if not turn_id:
        return None
    turn = session.get(AgentTurn, turn_id)
    if turn is None:
        return None
    return turn.topic_id or turn.id


def apply_proposal(session, pet, row, now=None):
    """确认 task_change 提案后生效（proposals.decide 分派；spec §3 状态转换绑定预期版本）。"""
    payload = json.loads(row.payload)
    action = payload['action']
    now = now or utc_now()
    if action == 'create':
        return _create_task(session, pet, row, payload['plan'], now)
    task = session.get(AgentTask, payload['taskId'])
    if task is None or task.pet_id != pet.id:
        raise TaskError('委托不存在', status=404)
    if task.current_version != payload['expectedVersion']:
        raise TaskError('委托计划已更新，请基于最新版本重新操作', status=409)
    if action in ('update', 'renew'):
        return _append_version(session, task, row, payload['plan'], now, renew=(action == 'renew'))
    if action == 'pause':
        return _transition(session, task, 'pause', now)
    if action == 'resume':
        return _transition(session, task, 'resume', now)
    return _transition(session, task, 'cancel', now)


def _create_task(session, pet, row, plan, now):
    task = AgentTask(pet_id=pet.id, task_key=secrets.token_hex(8), current_version=1,
                     timezone=plan['timezone'], status='observing',
                     deadline_at=iso_z_parse(plan['deadlineAt']),
                     effective_from=max(iso_z_parse(plan['startAt']), now),
                     topic_id=_topic_of(session, row.turn_id), proposal_id=row.id,
                     created_at=now, updated_at=now)
    if plan['kind'] == KIND_COMPARISON and plan.get('baselineStrategy') == 'wait_baseline' \
            and not baseline_ready(session, pet, plan):
        task.status = 'waiting_baseline'
        task.effective_from = now  # 等待期不计观察；进入观察时重设
    session.add(task)
    session.flush()
    session.add(AgentTaskPlan(task_id=task.id, version=1,
                              payload=json.dumps(plan, ensure_ascii=False),
                              proposal_id=row.id, created_at=now))
    session.flush()
    jobs.enqueue(session, CHECKPOINT_KIND, {'taskId': task.id},
                 dedup_key=f'task:{task.id}', next_run_at=now)
    return task


def _append_version(session, task, row, plan, now, renew):
    if task.status == 'cancelled':
        raise TaskError('委托已取消，不能修改或续期；可重新发起一条新委托', status=409)
    if task.status == 'completed' and not renew:
        raise TaskError('委托已到期交付，修改请改用续期（renew）或新开委托', status=409)
    version = task.current_version + 1
    session.add(AgentTaskPlan(task_id=task.id, version=version,
                              payload=json.dumps(plan, ensure_ascii=False),
                              proposal_id=row.id, created_at=now))
    task.current_version = version
    task.timezone = plan['timezone']
    task.deadline_at = iso_z_parse(plan['deadlineAt'])
    task.effective_from = max(iso_z_parse(plan['startAt']), task.effective_from, now)
    task.due_attempts = 0
    task.updated_at = now
    if task.status == 'completed':  # 续期重启观察（历史回信保留）
        task.status = 'observing'
        task.ended_at = None
        task.end_reason = None
        jobs.enqueue(session, CHECKPOINT_KIND, {'taskId': task.id},
                     dedup_key=f'task:{task.id}', next_run_at=now)
    elif task.status == 'waiting_baseline':
        task.effective_from = now  # 新版本等待期重新起算
    return task


def _transition(session, task, op, now):
    if op == 'pause':
        if task.status not in ('observing', 'waiting_baseline'):
            raise TaskError(f"当前状态（{STATUS_LABELS.get(task.status, task.status)}）不能暂停", status=409)
        intervals = json.loads(task.paused_intervals or '[]')
        intervals.append({'start': iso_z(now)})
        task.paused_intervals = json.dumps(intervals, ensure_ascii=False)
        task.status = 'paused'
    elif op == 'resume':
        if task.status != 'paused':
            raise TaskError(f"当前状态（{STATUS_LABELS.get(task.status, task.status)}）不在暂停中", status=409)
        intervals = json.loads(task.paused_intervals or '[]')
        if intervals and not intervals[-1].get('end'):
            intervals[-1]['end'] = iso_z(now)
        task.paused_intervals = json.dumps(intervals, ensure_ascii=False)
        plan = plan_of(session, task)
        pet = session.get(Pet, task.pet_id)
        if plan['kind'] == KIND_COMPARISON and plan.get('baselineStrategy') == 'wait_baseline' \
                and not baseline_ready(session, pet, plan):
            task.status = 'waiting_baseline'
        else:
            task.status = 'observing'
    else:  # cancel
        if task.status in ('cancelled', 'completed'):
            raise TaskError('委托已结束', status=409)
        intervals = json.loads(task.paused_intervals or '[]')
        if task.status == 'paused' and intervals and not intervals[-1].get('end'):
            intervals[-1]['end'] = iso_z(now)
            task.paused_intervals = json.dumps(intervals, ensure_ascii=False)
        task.status = 'cancelled'
        task.ended_at = now
        task.end_reason = 'cancelled'
        jobs.cancel(session, f'task:{task.id}')
    task.updated_at = now
    return task


def task_op(session, pet, task_id, op, now=None):
    """委托簿直接操作（D3：暂停/恢复/取消直接生效，不经确认卡）。幂等重复返回当前态。"""
    now = now or utc_now()
    if op not in ('pause', 'resume', 'cancel'):
        raise TaskError('op 需为 pause / resume / cancel', status=400)
    task = session.get(AgentTask, task_id)
    if task is None or task.pet_id != pet.id:
        raise TaskError('委托不存在', status=404)
    settled = {'pause': ('paused',), 'resume': ('observing', 'waiting_baseline'),
               'cancel': ('cancelled',)}[op]
    if task.status in settled and not (op == 'cancel' and task.status == 'completed'):
        return task  # 幂等：重复操作返回当前态
    return _transition(session, task, op, now)


# ── 检查点调度（jobs 执行器） ────────────────────────────────────────────────

def handle_checkpoint_job(job, now=None):
    """task.checkpoint 作业执行器：到期优先，普通检查按时间与数据水位合并（D5）。

    三段短事务（spec §3：模型运行前后各一短事务）：① 状态推进与证据物化；
    ② 模型评估（无事务）；③ 提交前重新核对取消/暂停/换版后落交付与状态。
    返回 jobs 重排 dict（保持 pending）或 None（作业完成）。
    """
    from petsense.agent import checkpoint as checkpoint_runner
    now = now or utc_now()
    task_id = (job.get('payload') or {}).get('taskId')
    with session_scope() as session:
        ctx = None
        task = session.get(AgentTask, task_id)
        if task is not None and task.status not in ('cancelled', 'completed'):
            pet = session.get(Pet, task.pet_id)
            if pet is not None:
                try:
                    plan = plan_of(session, task)
                    ctx = _plan_phase(session, task, pet, plan, now)
                except TaskError:
                    ctx = None
    if ctx is None or ctx['mode'] == 'skip':
        return {'next_run_at': ctx['reschedule']} if ctx else None
    outcome = checkpoint_runner.evaluate(ctx, now)  # 模型失败不抛：decision=None 带 error
    with session_scope() as session:
        task = session.get(AgentTask, task_id)
        if task is None or task.status in ('cancelled', 'completed') \
                or task.current_version != ctx['version']:
            return None  # 在途结果不投递（取消/完成/换版竞态，spec §3）
        pet = session.get(Pet, task.pet_id)
        result = _commit_phase(session, task, pet, ctx, outcome, now)
    flush_pending_pushes()
    return result


def _plan_phase(session, task, pet, plan, now):
    """第一段：状态推进（waiting_baseline 确定性转换）、节流判定与证据物化。"""
    if task.status == 'paused' and now < task.deadline_at:
        return {'mode': 'skip', 'reschedule': min(task.deadline_at, now + PAUSED_POLL),
                'version': task.current_version}
    if task.status == 'waiting_baseline':
        if baseline_ready(session, pet, plan):
            task.status = 'observing'
            task.observing_since = now
            task.effective_from = max(task.effective_from, now)
            task.updated_at = now
        elif now < task.deadline_at:
            return {'mode': 'skip', 'reschedule': min(task.deadline_at, now + WAIT_BASELINE_POLL),
                    'version': task.current_version}
    due = now >= task.deadline_at
    if not due:
        if plan['kind'] == KIND_REVIEW:
            return {'mode': 'skip', 'reschedule': task.deadline_at, 'version': task.current_version}
        if task.last_check_at and now - task.last_check_at < ORDINARY_MIN_INTERVAL:
            return {'mode': 'skip',
                    'reschedule': min(task.deadline_at, task.last_check_at + ORDINARY_MIN_INTERVAL),
                    'version': task.current_version}
    evidence = materialize(session, pet, task, plan, now)
    data_moved = bool(evidence['dataThrough']) and (
        task.watermark_at is None or iso_z_parse(evidence['dataThrough']) > task.watermark_at)
    if evidence['dataThrough']:
        task.watermark_at = iso_z_parse(evidence['dataThrough'])
    if not due and not data_moved:
        task.updated_at = now
        return {'mode': 'skip', 'reschedule': min(task.deadline_at, now + ORDINARY_MIN_INTERVAL),
                'version': task.current_version}
    task.last_check_at = now  # 本次将发起模型检查（普通节流以发起时刻计）
    task.updated_at = now
    return {'mode': 'due' if due else 'ordinary', 'task_id': task.id, 'pet_id': pet.id,
            'user_id': pet.user_id, 'version': task.current_version, 'plan': plan,
            'status': task.status, 'evidence': evidence, 'due_attempts': task.due_attempts}


def _commit_phase(session, task, pet, ctx, outcome, now):
    """第三段：提交前已复核取消/换版；按结果落交付、状态与归档（稳定键去重）。"""
    plan = ctx['plan']
    if ctx['mode'] == 'due':
        if (outcome or {}).get('decision') != 'letter':
            task.due_attempts += 1
            if task.due_attempts <= MAX_DUE_RETRIES:
                return {'next_run_at': now + DUE_RETRY_DELAY}
            delivery = _deliver(session, pet, task, plan, kind='degraded_letter', text=None,
                                evidence=ctx['evidence'], run_id=(outcome or {}).get('run_id'),
                                now=now, reason=(outcome or {}).get('error') or 'model_unavailable')
            _finish(task, 'completed', 'deadline', now)
            _push_later(pet, delivery)
            return None
        delivery = _deliver(session, pet, task, plan, kind='letter', text=outcome.get('text'),
                            evidence=ctx['evidence'], run_id=outcome.get('run_id'), now=now,
                            reason=None)
        memory_id = _archive_letter(session, pet, task, plan, delivery, ctx['evidence'], now)
        if memory_id:
            delivery.archived_memory_id = memory_id
        _finish(task, 'completed', 'deadline', now)
        _push_later(pet, delivery)
        return None
    # 普通检查：候选提醒须过确定性条件核对与当日实例去重，暂停态不投递
    if (outcome or {}).get('decision') == 'remind' and task.status == 'observing':
        condition = condition_state(plan, ctx['evidence'], now)
        tz = ZoneInfo(plan.get('timezone') or DEFAULT_TZ)
        instance = f"remind:{now.astimezone(tz).date().isoformat()}"
        if condition.get('met') and session.execute(
                select(AgentTaskDelivery.id).where(AgentTaskDelivery.task_id == task.id,
                                                   AgentTaskDelivery.instance_key == instance)
        ).scalar_one_or_none() is None:
            delivery = _deliver(session, pet, task, plan, kind='reminder', text=outcome.get('text'),
                                evidence=ctx['evidence'], run_id=outcome.get('run_id'), now=now,
                                reason=None)
            _push_later(pet, delivery)
    return {'next_run_at': min(task.deadline_at, now + ORDINARY_MIN_INTERVAL)}


def _finish(task, status, reason, now):
    task.status = status
    task.ended_at = now if status in ('completed', 'cancelled') else task.ended_at
    task.end_reason = reason
    task.updated_at = now


def _deliver(session, pet, task, plan, *, kind, text, evidence, run_id, now, reason):
    """落一条交付 + 后台回合（同事务；instance_key 唯一键为稳定结果键，spec §3）。"""
    from petsense.agent import deliver as deliver_service
    if kind == 'reminder':
        tz = ZoneInfo(plan.get('timezone') or DEFAULT_TZ)
        instance = f"remind:{now.astimezone(tz).date().isoformat()}"  # 条件实例=委托+上海/计划日
    else:
        instance = f'letter:v{task.current_version}'               # 回信实例=委托版本
    existing = session.execute(select(AgentTaskDelivery).where(
        AgentTaskDelivery.task_id == task.id,
        AgentTaskDelivery.instance_key == instance)).scalar_one_or_none()
    if existing is not None:
        return existing
    content = text or _degraded_letter_text(plan, evidence, reason, now)
    payload = {'text': content, 'windowStart': evidence.get('windowStart'),
               'windowEnd': evidence.get('windowEnd'),
               'observedSeconds': evidence.get('observedSeconds'),
               'gapSeconds': evidence.get('gapSeconds'),
               'pausedSeconds': evidence.get('pausedSeconds'),
               'walkSeconds': evidence.get('walkSeconds'),
               'soundEventCount': evidence.get('soundEventCount'),
               'degraded': kind == 'degraded_letter'}
    if kind == 'reminder':
        payload['condition'] = condition_state(plan, evidence, now)
    delivery = AgentTaskDelivery(task_id=task.id, plan_version=task.current_version, kind=kind,
                                 instance_key=instance, run_id=run_id,
                                 payload=json.dumps(payload, ensure_ascii=False), created_at=now)
    session.add(delivery)
    session.flush()
    sources = [{'type': 'observations', 'deviceId': pet.device_id,
                'startAt': evidence.get('windowStart'), 'endAt': evidence.get('windowEnd'),
                'observedSeconds': evidence.get('observedSeconds'),
                'soundEventCount': evidence.get('soundEventCount')}] if kind != 'reminder' else []
    blocks = [{'type': 'text', 'speaker': 'assistant', 'text': content},
              {'type': 'task_result', 'task': {'taskId': task.id, 'deliveryId': delivery.id,
                                               'kind': kind, 'title': plan.get('title'),
                                               'planKind': plan['kind'],
                                               'version': task.current_version,
                                               'deadlineAt': iso_z(task.deadline_at),
                                               'degraded': kind == 'degraded_letter'}}]
    turn, _created = deliver_service.deliver_background_message(
        session, pet.id, f'task-{delivery.id}', blocks, topic_id=task.topic_id,
        sources=sources, now=now)
    delivery.turn_id = turn.id
    return delivery


def _degraded_letter_text(plan, evidence, reason, now):
    """D6 降级回信：有覆盖与缺口、明确注明评估未完成，不伪造结论。"""
    minutes = int((evidence.get('observedSeconds') or 0) // 60)
    paused = int((evidence.get('pausedSeconds') or 0) // 60)
    why = {'model_unavailable': '模型评估暂不可用',
           'model_invalid_output': '评估结果不合法'}.get(reason or '', '评估未能完成')
    paused_note = f'，其中暂停约 {paused} 分钟' if paused else ''
    return (f"关于「{plan.get('question') or plan.get('title')}」：观察已到截止时间，"
            f'但本次评估未能完成（{why}）。观察期间有效覆盖约 {minutes} 分钟{paused_note}，'
            '其余时间为未观测缺口。这封回信不包含结论，不代表它的状态正常或异常；'
            '如仍需要结论，可以重新发起同样的委托。')


def _archive_letter(session, pet, task, plan, delivery, evidence, now):
    """正常回信归档为长期经历（D6：只有具备事实依据的正常回信归档；按回信去重）。"""
    from petsense.agent import memory as pet_memory
    from petsense.db.models.memory import PetMemory
    marker = json.dumps({'type': 'delivery', 'id': delivery.id}, ensure_ascii=False)
    existing = session.execute(select(PetMemory.id).where(
        PetMemory.pet_id == pet.id, PetMemory.source_refs.like(f'%{marker}%'))).scalar_one_or_none()
    if existing is not None:
        return existing
    days = len(evidence.get('daily') or [])
    minutes = int((evidence.get('observedSeconds') or 0) // 60)
    if plan.get('feature') == 'actions':
        metric = f"，累计走动 {int((evidence.get('walkSeconds') or 0) // 60)} 分钟"
    elif plan.get('feature') == 'sounds':
        metric = f"，声音事件 {evidence.get('soundEventCount') or 0} 次"
    else:
        metric = ''
    summary = f"委托「{plan.get('title')}」已回信：观察 {days} 天、有效覆盖 {minutes} 分钟{metric}。"
    window_start = iso_z_parse(evidence['windowStart']) if evidence.get('windowStart') else None
    window_end = iso_z_parse(evidence['windowEnd']) if evidence.get('windowEnd') else None
    if window_start is not None and window_end is not None and window_end <= window_start:
        window_start = window_end = None  # 极短/空窗（确认后立刻到期）不写时间范围
    entry, error = pet_memory.save_entry(
        session, pet.id, kind='task_result', subject='pet',
        summary=summary[:pet_memory.MAX_SUMMARY_CHARS],
        start_at=window_start, end_at=window_end,
        facts={'taskId': task.id, 'deliveryId': delivery.id, 'planVersion': task.current_version,
               'kind': plan['kind'], 'observedMinutes': minutes,
               'walkSeconds': evidence.get('walkSeconds'),
               'soundEventCount': evidence.get('soundEventCount')},
        source_type='worker',
        source_refs=[{'type': 'task', 'id': task.id},
                     {'type': 'delivery', 'id': delivery.id}]
                    + ([{'type': 'run', 'id': delivery.run_id}] if delivery.run_id else []),
        generated_at=now)
    return None if error else entry.id


def _push_later(pet, delivery):
    """记录待推帧目标：交付事务提交后由 flush_pending_pushes 推送（事务内不推 WS）。"""
    _PENDING_PUSHES.append((pet.user_id, pet.id, delivery.turn_id))


def flush_pending_pushes():
    """提交后推帧（handle_checkpoint_job 在事务外调用）。App 不在线时静默。"""
    from petsense.agent import deliver as deliver_service
    while _PENDING_PUSHES:
        user_id, pet_id, turn_id = _PENDING_PUSHES.pop(0)
        with session_scope() as session:
            turn = session.get(AgentTurn, turn_id)
            if turn is not None:
                deliver_service.push_delivered(user_id, pet_id, turn)


# ── 对外读取（工具 / 委托簿 / 快照） ─────────────────────────────────────────

def delivery_public(row):
    payload = json.loads(row.payload)
    return {'id': row.id, 'taskId': row.task_id, 'kind': row.kind,
            'planVersion': row.plan_version, 'text': payload.get('text'),
            'windowStart': payload.get('windowStart'), 'windowEnd': payload.get('windowEnd'),
            'observedMinutes': int((payload.get('observedSeconds') or 0) // 60),
            'gapMinutes': int((payload.get('gapSeconds') or 0) // 60),
            'pausedMinutes': int((payload.get('pausedSeconds') or 0) // 60),
            'walkSeconds': payload.get('walkSeconds'),
            'soundEventCount': payload.get('soundEventCount'),
            'condition': payload.get('condition'), 'degraded': bool(payload.get('degraded')),
            'turnId': row.turn_id, 'runId': row.run_id, 'archivedMemoryId': row.archived_memory_id,
            'createdAt': iso_z(row.created_at)}


def public(session, task, include_detail=False):
    plan = plan_of(session, task)
    deliveries = session.execute(select(AgentTaskDelivery).where(
        AgentTaskDelivery.task_id == task.id).order_by(AgentTaskDelivery.id)).scalars().all()
    result = {'id': task.id, 'taskKey': task.task_key, 'status': task.status,
              'statusLabel': STATUS_LABELS.get(task.status, task.status),
              'kind': plan['kind'], 'kindLabel': KIND_LABELS.get(plan['kind'], plan['kind']),
              'title': plan.get('title'), 'question': plan.get('question'),
              'feature': plan.get('feature'),
              'featureLabel': FEATURE_LABELS.get(plan.get('feature')) if plan.get('feature') else None,
              'condition': plan.get('condition'), 'comparison': plan.get('comparison'),
              'baselineStrategy': plan.get('baselineStrategy'), 'timezone': plan.get('timezone'),
              'startAt': plan.get('startAt'), 'deadlineAt': iso_z(task.deadline_at),
              'effectiveFrom': iso_z(task.effective_from),
              'currentVersion': task.current_version, 'topicId': task.topic_id,
              'pausedIntervals': json.loads(task.paused_intervals or '[]'),
              'reminders': sum(1 for d in deliveries if d.kind == 'reminder'),
              'letters': sum(1 for d in deliveries if d.kind != 'reminder'),
              'lastDeliveryAt': iso_z(deliveries[-1].created_at) if deliveries else None,
              'createdAt': iso_z(task.created_at), 'updatedAt': iso_z(task.updated_at),
              'endedAt': iso_z(task.ended_at) if task.ended_at else None,
              'endReason': task.end_reason}
    if include_detail:
        result['deliveries'] = [delivery_public(d) for d in deliveries]
        result['versions'] = [
            {'version': row.version, 'plan': json.loads(row.payload), 'createdAt': iso_z(row.created_at)}
            for row in session.execute(select(AgentTaskPlan).where(
                AgentTaskPlan.task_id == task.id).order_by(AgentTaskPlan.version)).scalars()]
    return result


def list_tasks(session, pet):
    rows = session.execute(select(AgentTask).where(AgentTask.pet_id == pet.id)
                           .order_by((AgentTask.status == 'completed').desc(), AgentTask.id.desc())
                           ).scalars().all()
    return [public(session, row) for row in rows]


def read_tool_payload(session, pet, task_id=None):
    """read_task 工具结果：本宠委托及交付的鉴权只读查询（P00 §5）。"""
    if task_id is not None:
        task = session.get(AgentTask, task_id)
        if task is None or task.pet_id != pet.id:
            return {'error': '委托不存在或不在当前宠物名下'}
        return public(session, task, include_detail=True)
    rows = session.execute(select(AgentTask).where(AgentTask.pet_id == pet.id)
                           .order_by(AgentTask.id.desc())).scalars().all()
    return {'tasks': [public(session, row) for row in rows],
            'note': '状态含义：waiting_baseline=等待个人基线就绪、observing=观察中、'
                    'paused=已暂停（截止不变）、completed=已交付回信、cancelled=已取消'}


def active_summaries(session, pet_id, now=None):
    """氛围快照的活跃目标行（spec §2：活跃目标摘要进入氛围快照）。"""
    now = now or utc_now()
    rows = session.execute(select(AgentTask).where(
        AgentTask.pet_id == pet_id,
        AgentTask.status.in_(('observing', 'waiting_baseline', 'paused')))).scalars().all()
    result = []
    for task in rows:
        try:
            plan = plan_of(session, task)
        except TaskError:
            continue
        local = task.deadline_at.astimezone(ZoneInfo(plan.get('timezone') or DEFAULT_TZ))
        tag = {'waiting_baseline': '，等基线', 'paused': '，暂停中'}.get(task.status, '')
        result.append(f"{plan.get('title')}（{local.strftime('%m-%d %H:%M')} 截止{tag}）")
    return result[:3]
