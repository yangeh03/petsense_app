"""工具注册表与权限矩阵（W01，agent-w01-spec.md §3.4）。

声明与执行分离注册；权限校验先于执行（存在性 → 运行形态 → stance 范围），
越权调用返回结构化拒绝作为工具结果，模型据此如实表达，拒绝事件由调用方入轨迹。
新增能力 = 注册工具 + 更新系统提示与权限矩阵，不改主循环（V11 §5.1）；
新增工具须先改 P00 契约再注册（roadmap §4.2）。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from petsense.agent import baseline as baseline_service
from petsense.agent.observations import query as observations_query
from petsense.common import shanghai_now
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Pet, TelemetryEvent, TelemetryLatest, TelemetryMinute
from petsense.telemetry.status import latest_telemetry
from petsense.uwb.service import service as uwb_service
from sqlalchemy import func, select

DIALOGUE = 'dialogue'
FACT_READ = 'fact_read'
TOOL_TIMEOUT_S = 15.0

_TOOLS = {}


class ToolSpec:
    def __init__(self, name, description, parameters, group, forms, execute):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.group = group
        self.forms = tuple(forms)
        self.execute = execute

    def schema(self):
        return {'type': 'function', 'name': self.name,
                'description': self.description, 'parameters': self.parameters}


def register(spec):
    _TOOLS[spec.name] = spec


def schemas_for(form, stance_scope):
    """渲染该运行形态与范围下可用的工具 schema。out_of_scope 下业务与网络工具全部不可用（V11 §12.1）。"""
    if stance_scope == 'out_of_scope':
        return []
    return [spec.schema() for spec in _TOOLS.values() if form in spec.forms]


def _parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
    return parsed


def _execute_query_observations(args, context):
    start = _parse_time(args.get('start'))
    end = _parse_time(args.get('end'))
    if start is None or end is None:
        return {'error': '请提供可解析的起止时间；缺失时区按 Asia/Shanghai 解释，范围不得超过90天'}
    granularity = args.get('granularity') if args.get('granularity') in ('minute', 'summary') else 'minute'
    with session_scope() as s:
        pet = s.get(Pet, context['pet_id'])
        if pet is None or pet.user_id != context['user_id']:
            raise ValueError('宠物归属已变化')
        result = observations_query(s, pet, start, end)
    if granularity == 'summary':
        result.pop('soundEvents', None)
        result.pop('latest', None)
    return result


register(ToolSpec(
    name='query_observations',
    description='查询本宠项圈在指定时间窗口的真实观测：动作时长分布、声音事件、有效体征、'
                '覆盖与缺口。窗口最多90天；按 Asia/Shanghai 解释自然日；start 包含、end 不包含，'
                '应为 ISO 时间（缺失时区按 Asia/Shanghai）；granularity=summary 时省略声音事件明细。'
                '数据缺失不能推断为正常，查询失败不等于没有记录。',
    parameters={'type': 'object', 'properties': {
        'start': {'type': 'string', 'description': '起始时间（含），ISO 时间'},
        'end': {'type': 'string', 'description': '结束时间（不含），ISO 时间'},
        'granularity': {'type': 'string', 'enum': ['minute', 'summary'],
                        'description': 'minute=分钟汇总（默认）；summary=仅窗口总量'}},
        'required': ['start', 'end'], 'additionalProperties': False},
    group=FACT_READ, forms=(DIALOGUE,), execute=_execute_query_observations))


def _execute_read_baseline(args, context):
    feature = args.get('feature')
    features = [feature] if feature in ('actions', 'sounds') else None
    date = args.get('date')
    date = date.strip() if isinstance(date, str) else None
    with session_scope() as s:
        pet = s.get(Pet, context['pet_id'])
        if pet is None or pet.user_id != context['user_id']:
            raise ValueError('宠物归属已变化')
        try:
            return baseline_service.read_baseline(s, pet, features=features, date_str=date)
        except ValueError as exc:
            return {'error': f'{exc}。请修正参数后重试（date 为上海日期 YYYY-MM-DD，不晚于今天）。'}


register(ToolSpec(
    name='read_baseline',
    description='查询本宠的个人基线与认识进度：认识中（不足7个有效日）只有进度、没有个人参照范围，'
                '不得说「比平时」；已就绪时给出全天与四个时段（夜间/上午/下午/晚间）的个人参照范围'
                '（p10–p90 与中位数）以及当天（或指定日期）实际值、覆盖与能否比较。'
                'feature 缺省查动作与声音两个维度；date 缺省今天（上海日期 YYYY-MM-DD，不晚于今天）。'
                '参照范围与当日数值只能来自本工具结果，覆盖不足的时段不能当作活动减少或增加。',
    parameters={'type': 'object', 'properties': {
        'feature': {'type': 'string', 'enum': ['actions', 'sounds'],
                    'description': '维度：actions=动作（走动），sounds=声音（叫声等事件）'},
        'date': {'type': 'string', 'description': '对比日期，上海日期 YYYY-MM-DD，缺省今天'}},
        'additionalProperties': False},
    group=FACT_READ, forms=(DIALOGUE,), execute=_execute_read_baseline))


def _bucket_actions(rows, start):
    """窗口内分钟行按 5 分钟分桶（spec §3.5）：topAction + 各动作秒数 + 观测秒数。"""
    buckets = {}
    for row in rows:
        index = int((row.minute - start).total_seconds() // 300)
        bucket = buckets.setdefault(index, {'actions': {}, 'observedSeconds': 0})
        bucket['observedSeconds'] += min(60, row.observed_seconds)
        for key, value in json.loads(row.actions).items():
            bucket['actions'][key] = bucket['actions'].get(key, 0) + value
    result = []
    for index in sorted(buckets)[:48]:
        bucket = buckets[index]
        top = max(bucket['actions'], key=bucket['actions'].get) if bucket['actions'] else None
        result.append({'startAt': iso_z(start + timedelta(seconds=300 * index)),
                       'topAction': top, 'actionSeconds': bucket['actions'],
                       'observedSeconds': bucket['observedSeconds']})
    return result


def _today_summary(session, pet, end):
    """今日统计（D2 同口径）：复用 observations.query，当日（上海零点起）现算。"""
    start = shanghai_now(end).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if end <= start:  # 恰逢上海零点：窗口为空，如实给零值
        actions = {}
        observed_seconds = 0
        sound_count = 0
    else:
        summary = observations_query(session, pet, start, end)
        actions = summary.get('actionSeconds') or {}
        observed_seconds = summary.get('observedSeconds') or 0
        sound_count = summary.get('soundEventCount') or 0
    return {'windowStart': iso_z(start), 'walkSeconds': actions.get('走动', 0),
            'actionSeconds': actions, 'observedMinutes': int(observed_seconds // 60),
            'soundEventCount': sound_count}


def _current_status(session, pet, start, end):
    """窗口内 DB 节（动作序列/声音/室外定位/今日）；设备详情与室内定位由调用方在会话外补充——
    session_scope 为线程级共享会话，latest_telemetry/uwb_service 各自开会话，嵌套会关闭外层事务。"""
    window = {'startAt': iso_z(start), 'endAt': iso_z(end),
              'minutes': int((end - start).total_seconds() // 60)}
    source = {'type': 'current_status', 'deviceId': pet.device_id,
              'startAt': iso_z(start), 'endAt': iso_z(end)}
    if not pet.device_id:
        return {'window': window, 'today': _today_summary(session, pet, end),
                'actions': [], 'sounds': {'events': [], 'count': 0},
                'location': {'indoor': None, 'outdoor': None}, 'source': source}
    rows = session.execute(select(TelemetryMinute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute < end)
        .order_by(TelemetryMinute.minute)).scalars().all()
    episodes = session.execute(select(TelemetryEvent).where(
        TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
        TelemetryEvent.started_at < end, TelemetryEvent.last_seen_at >= start)
        .order_by(TelemetryEvent.started_at.desc()).limit(20)).scalars().all()
    sound_count = session.execute(select(func.count()).select_from(TelemetryEvent).where(
        TelemetryEvent.owner_id == pet.user_id, TelemetryEvent.device_id == pet.device_id,
        TelemetryEvent.started_at < end, TelemetryEvent.last_seen_at >= start)).scalar_one()
    latest_row = session.get(TelemetryLatest, pet.device_id)
    outdoor = None
    if latest_row is not None and latest_row.owner_id == pet.user_id:
        try:
            payload = json.loads(latest_row.payload)
            if isinstance(payload.get('gps'), dict):
                outdoor = payload['gps']
        except (TypeError, ValueError):
            outdoor = None
    return {'window': window, 'actions': _bucket_actions(rows, start),
            'sounds': {'count': sound_count,
                       'events': [{'label': e.label, 'startedAt': iso_z(e.started_at),
                                   'lastSeenAt': iso_z(e.last_seen_at),
                                   'experimental': bool(e.experimental)} for e in episodes]},
            'location': {'indoor': None, 'outdoor': outdoor},
            'today': _today_summary(session, pet, end), 'source': source}


def _execute_current_status(args, context):
    raw = args.get('minutes')
    if isinstance(raw, bool) or not isinstance(raw, int):
        minutes = 30
    else:
        minutes = min(240, max(1, raw))
    end = utc_now()
    start = end - timedelta(minutes=minutes)
    with session_scope() as s:
        pet = s.get(Pet, context['pet_id'])
        if pet is None or pet.user_id != context['user_id']:
            raise ValueError('宠物归属已变化')
        device_id = pet.device_id
        result = _current_status(s, pet, start, end)
    result['device'] = (latest_telemetry(device_id) or {'state': 'no_data'}) if device_id else {'state': 'unbound'}
    if isinstance(result['device'], dict) and result['device'].get('valid') is False:
        # 无效帧的 0 是占位不是读数（W02 复验 run14：离线帧 batteryPercent=0 被转述成「没电」）。
        # 降级为陈旧无效状态，只保留时间与在线事实，不给占位数值。
        stale = result['device']
        result['device'] = {'state': 'stale_invalid', 'online': stale.get('online'),
                            'receivedAt': stale.get('receivedAt'), 'valid': False,
                            'note': '最后回传帧的读数无效：心率/血氧/电量等 0 值是占位，不能当作真实数值'}
    try:
        result['location']['indoor'] = uwb_service.latest(device_id) if device_id else None
    except Exception:
        result['location']['indoor'] = None  # UWB 为内存态服务，异常时如实缺省，不阻断其余节
    return result


register(ToolSpec(
    name='read_current_status',
    description='获取本宠当前的实时状态明细：体征与设备详情、今日统计（当日零点起）、'
                '最近一段时间的动作序列（5分钟分桶）、最近的声音事件、最新室内外定位（含坐标与质量）。'
                '快照只有氛围一行，实时数值以本工具为准；窗口按分钟数回看，默认30、最大240。',
    parameters={'type': 'object', 'properties': {
        'minutes': {'type': 'integer', 'minimum': 1, 'maximum': 240,
                    'description': '回看窗口（分钟），默认30'}},
        'additionalProperties': False},
    group=FACT_READ, forms=(DIALOGUE,), execute=_execute_current_status))


def execute_tool(name, arguments, form, stance_scope, context):
    """权限矩阵先于执行：存在性 → 形态 → 范围；通过后带超时执行。

    返回 {'allowed', 'reason', 'result', 'duration_ms'}；拒绝与失败都以结构化
    result 交给模型（不抛裸异常，V11 §5.1），由调用方记入轨迹。
    """
    started = time.monotonic()
    spec = _TOOLS.get(name)
    if spec is None:
        return {'allowed': False, 'reason': 'unknown_tool', 'duration_ms': 0,
                'result': {'error': f'工具 {name} 不存在。可用工具以系统提示中的清单为准，'
                                    '不得声称已执行不存在的工具。'}}
    if form not in spec.forms:
        return {'allowed': False, 'reason': 'form_not_allowed', 'duration_ms': 0,
                'result': {'error': f'工具 {name} 不适用于当前运行形态。'}}
    if stance_scope == 'out_of_scope':
        return {'allowed': False, 'reason': 'scope_not_allowed', 'duration_ms': 0,
                'result': {'error': '当前请求不在服务范围内，业务与联网工具不可用。'}}
    if stance_scope is None:
        return {'allowed': False, 'reason': 'stance_missing', 'duration_ms': 0,
                'result': {'error': '缺少范围声明：请先在回复正文首行输出单行 JSON '
                                    '({"scope":"allowed|out_of_scope","intent":"...","speaker":"pet|assistant"})，'
                                    '然后重新发起同样的工具调用（声明行在前即可，不要放弃查询）。'}}
    if not isinstance(arguments, dict):
        return {'allowed': False, 'reason': 'bad_arguments', 'duration_ms': 0,
                'result': {'error': '工具参数不是合法 JSON 对象，请修正后重试。'}}
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(spec.execute, arguments, context).result(timeout=TOOL_TIMEOUT_S)
    except FutureTimeout:
        return {'allowed': True, 'reason': 'timeout', 'duration_ms': int((time.monotonic() - started) * 1000),
                'result': {'error': f'工具执行超时（>{TOOL_TIMEOUT_S:.0f}s）。请如实向主人说明未能查到，不得当作没有记录。'}}
    except Exception as exc:
        return {'allowed': True, 'reason': 'error', 'duration_ms': int((time.monotonic() - started) * 1000),
                'result': {'error': f'工具执行失败（{type(exc).__name__}）。请如实向主人说明查询失败，不得当作没有记录。'}}
    return {'allowed': True, 'reason': None, 'duration_ms': int((time.monotonic() - started) * 1000),
            'result': result}


def tool_names():
    return sorted(_TOOLS)
