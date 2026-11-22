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

from petsense.agent.observations import query as observations_query
from petsense.agent import proposals as proposal_service
from petsense.agent import settings as settings_service
from petsense.common import shanghai_now
from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models import Pet, TelemetryEvent, TelemetryLatest, TelemetryMinute
from petsense.telemetry.status import latest_telemetry
from petsense.uwb.service import service as uwb_service
from sqlalchemy import func, select

DIALOGUE = 'dialogue'
FACT_READ = 'fact_read'
PROFILE = 'profile'      # 确认门组（W03）：明确指令直写 / 候选确认卡，两路径在执行器内（agent-w03-spec.md §3.4）
SETTINGS = 'settings'    # 直写组（W03）：助手设定，明确指令即改即回显
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


def _execute_read_pet_profile(args, context):
    with session_scope() as s:
        pet = s.get(Pet, context['pet_id'])
        if pet is None or pet.user_id != context['user_id']:
            raise ValueError('宠物归属已变化')
        return proposal_service.profile_payload(s, pet)


register(ToolSpec(
    name='read_pet_profile',
    description='读取本宠的完整档案：名字、物种、品种、生日（带精度：精确日期或按年/月估算）与推导年龄、'
                '最新体重与最近体重记录。档案未填写的字段返回 null，如实说明「档案里还没有」，不得猜测。',
    parameters={'type': 'object', 'properties': {}, 'additionalProperties': False},
    group=FACT_READ, forms=(DIALOGUE,), execute=_execute_read_pet_profile))


def _execute_prepare_profile_update(args, context):
    fields, explicit = args.get('fields'), bool(args.get('explicit'))
    if not isinstance(fields, dict):
        return {'error': 'fields 需为对象，包含要保存的档案字段'}
    try:
        with session_scope() as s:
            pet = s.get(Pet, context['pet_id'])
            if pet is None or pet.user_id != context['user_id']:
                raise ValueError('宠物归属已变化')
            row, updated, refreshed = proposal_service.create(
                s, pet, context.get('turn_id'), fields, explicit)
    except proposal_service.ProposalError as exc:
        return {'error': exc.message, 'validationErrors': exc.errors,
                'guidance': '候选未通过校验，未保存。请向主人如实说明原因；主人补充后可重新发起。'}
    # 旧 pending 卡随 supersede 翻态的回合 id：routes 提交后推 turn 帧刷新（§3.5）
    refreshed_ids = [tid for tid in refreshed if tid != context.get('turn_id')]
    if explicit:
        return {'written': True, 'proposal': proposal_service.public(row),
                'updatedFields': updated, 'refreshedTurnIds': refreshed_ids,
                'guidance': '已写入档案。请如实回显保存结果，与 updatedFields 一致。'}
    return {'written': False, 'proposal': proposal_service.public(row),
            'refreshedTurnIds': refreshed_ids,
            'guidance': '已生成待确认候选（确认卡会出现在回复中）。未经主人确认不得声称已保存；'
                        '主人要修改候选时，按新内容重新调用本工具生成新版本。'}


register(ToolSpec(
    name='prepare_profile_update',
    description='保存本宠档案事实（生日、体重、名字、品种）。两条生效路径：主人明确指令'
                '（「记住/更新为…」且主体、内容、时间清楚）传 explicit=true 直接写入并回显；'
                '信息不完全确定（如只说「它三岁了」）传 explicit=false 生成待确认候选卡，'
                '由主人在卡片上确认。生日给具体日期用 birthDate（YYYY-MM-DD 或 YYYY-MM 或 YYYY），'
                '只有年龄/大致年份用 birthYear；体重换算为公斤传 weightKg（如「九斤」传 4.5），'
                '并在 weightRawText 保留原始表述。其他主体（朋友家的宠物）、假设（「假如」）、'
                '否定表述不得调用本工具。',
    parameters={'type': 'object', 'properties': {
        'fields': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': '宠物名字（1—20 字）'},
            'breed': {'type': 'string', 'description': '品种（≤40 字）'},
            'birthDate': {'type': 'string', 'description': '生日：2023-09-10 / 2023-09 / 2023'},
            'birthDatePrecision': {'type': 'string', 'enum': ['day', 'month', 'year'],
                                   'description': '生日精度；缺省按日期格式推导，只允许降档'},
            'birthYear': {'type': 'integer', 'description': '年龄/大致年份估算（如三岁→约 2023），与 birthDate 二选一'},
            'weightKg': {'type': 'number', 'description': '体重（公斤，0.1–100）'},
            'weightMeasuredOn': {'type': 'string', 'description': '体重发生日期 YYYY-MM-DD，缺省今天'},
            'weightRawText': {'type': 'string', 'description': '主人的原始表述（如「九斤」），≤40 字'}},
            'additionalProperties': False},
        'explicit': {'type': 'boolean', 'description': '主人是否明确指令保存'},
        'note': {'type': 'string', 'description': '提案理由（入运行轨迹）'}},
        'required': ['fields', 'explicit'], 'additionalProperties': False},
    group=PROFILE, forms=(DIALOGUE,), execute=_execute_prepare_profile_update))


def _execute_prepare_role_settings(args, context):
    try:
        patch = settings_service.validate_patch(args)
    except ValueError as exc:
        return {'error': str(exc), 'guidance': '设定未通过校验，未修改。请如实说明原因。'}
    with session_scope() as s:
        row = settings_service.persist(s, context['user_id'], patch)
        return {'updated': True, 'settings': settings_service.public(row),
                'guidance': '设定已生效并从下一轮对话开始按新设定表达；请向主人回显修改结果。'}


register(ToolSpec(
    name='prepare_role_settings',
    description='修改照料助手设定（名字、对主人的称呼、回答详略、主动程度）。仅在主人明确表达'
                '修改意图时调用并直接生效；商量或疑问（「你能叫小禾吗？」）先向主人确认再调用。'
                '主动程度 0=安静、1=标准、2=多提醒。清空称呼传空字符串。',
    parameters={'type': 'object', 'properties': {
        'assistantName': {'type': 'string', 'description': '助手名字（1—20 字）'},
        'ownerTitle': {'type': 'string', 'description': '对主人的称呼（≤20 字，空串=清除）'},
        'verbosity': {'type': 'string', 'enum': ['concise', 'detailed'], 'description': '简洁 / 详细'},
        'proactivity': {'type': 'integer', 'enum': [0, 1, 2], 'description': '主动程度三档'}},
        'additionalProperties': False},
    group=SETTINGS, forms=(DIALOGUE,), execute=_execute_prepare_role_settings))


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
