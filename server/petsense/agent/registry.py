"""工具注册表与权限矩阵（W01，agent-w01-spec.md §3.4；W06 read_web_page/EXTERNAL_READ）。

声明与执行分离注册；权限校验先于执行（存在性 → 运行形态 → stance 范围），
越权调用返回结构化拒绝作为工具结果，模型据此如实表达，拒绝事件由调用方入轨迹。
新增能力 = 注册工具 + 更新系统提示与权限矩阵，不改主循环（V11 §5.1）；
新增工具须先改 P00 契约再注册（roadmap §4.2）。
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from petsense.agent import actions as actions_service
from petsense.agent import baseline as baseline_service
from petsense.agent import fetch
from petsense.agent import memory as pet_memory
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
CHECKPOINT = 'checkpoint'  # W08 检查点形态：仅事实读取工具可用（agent-w08-spec.md §3）
REVIEW = 'review'        # W09 周期回顾形态：只挂事实类工具（个人基线比较留给 W11）
EVENT = 'event'          # W09 硬件事件形态：只挂事实类工具
FACT_READ = 'fact_read'
MEMORY = 'memory'
PROFILE = 'profile'      # 确认门组（W03）：明确指令直写 / 候选确认卡，两路径在执行器内（agent-w03-spec.md §3.4）
SETTINGS = 'settings'    # 直写组（W03）：助手设定，明确指令即改即回显
EXTERNAL_READ = 'external_read'  # W06：公网网页深读（联网知识组）
DEVICE_CONTROL = 'device_control'  # W10：设备动作（仅录音），D3 开关关闭时不进 schemas
ACTION = 'action'        # W10 动作组（日记/草稿）：日记生成走确认卡，草稿明确指令可直写（D6）
TASK = 'task'            # W08 委托组：read_task 只读 / propose_task_change 只生成候选（P00 §5）
TOOL_TIMEOUT_S = 15.0

_TOOLS = {}


class ToolSpec:
    def __init__(self, name, description, parameters, group, forms, execute, timeout_s=TOOL_TIMEOUT_S):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.group = group
        self.forms = tuple(forms)
        self.execute = execute
        self.timeout_s = timeout_s

    def schema(self):
        return {'type': 'function', 'name': self.name,
                'description': self.description, 'parameters': self.parameters}


def register(spec):
    _TOOLS[spec.name] = spec


def schemas_for(form, stance_scope, search_enabled=True, recording_enabled=None):
    """渲染该运行形态与范围下可用的工具 schema。out_of_scope 下业务与网络工具全部不可用（V11 §12.1）。
    search_enabled=False（PETSENSE_SEARCH_DISABLED 灰度开关）时 EXTERNAL_READ 组不进 schemas；
    recording_enabled 缺省解析 W10 D3 开关（PETSENSE_AGENT_RECORDING_ENABLED，默认关停），
    关闭时 DEVICE_CONTROL 组不进 schemas——能力描述与真实可用性保持一致
    （W06 spec §4.5、agent-w10-spec.md §3）。"""
    if stance_scope == 'out_of_scope':
        return []
    if recording_enabled is None:
        recording_enabled = actions_service.recording_enabled()
    return [spec.schema() for spec in _TOOLS.values()
            if form in spec.forms and (search_enabled or spec.group != EXTERNAL_READ)
            and (recording_enabled or spec.group != DEVICE_CONTROL)]


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
    group=FACT_READ, forms=(DIALOGUE, CHECKPOINT, REVIEW, EVENT),
    execute=_execute_query_observations))


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
    group=FACT_READ, forms=(DIALOGUE, CHECKPOINT), execute=_execute_read_baseline))


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
    group=FACT_READ, forms=(DIALOGUE, REVIEW, EVENT), execute=_execute_current_status))


def _pet_of(session, context):
    pet = session.get(Pet, context['pet_id'])
    if pet is None or pet.user_id != context['user_id']:
        raise ValueError('宠物归属已变化')
    return pet


def _execute_write_memory(args, context):
    with session_scope() as s:
        pet = _pet_of(s, context)
        entry, error = pet_memory.save_model_write(s, pet.id, args, context)
        if error:
            return {'saved': False, 'error': error}
        return {'saved': True, 'memoryId': entry.id, 'kind': entry.kind,
                'subject': entry.subject, 'version': entry.version,
                'summary': entry.summary, 'certainty': entry.certainty,
                'note': '写入成功后可向主人轻量回显（如「这个我记下了」）'}


register(ToolSpec(
    name='write_memory',
    description='把值得跨天记住的内容写入长期记忆（自主写）。允许：主人讲述的真实经历或事件'
                '（episode=宠物的经历 / owner_event=主人自己的事）、反复出现的规律'
                '（habit_candidate，不确定时标 tentative=true）、本轮明确的照顾安排类要点'
                '（session_takeaway，默认不记）。禁止：假设（假如/如果）、否定句式的转述、'
                '其他主体（朋友家的宠物）、长期偏好（喜欢/不喜欢某物，属确认写、尚未接入）、'
                '爱宠人格的拟人表达。发生时间未知就不传时间参数，不要用当前时间冒充。'
                '纠正旧记忆时传 supersedesId=旧记忆id（新条目必须与被替换条目同类型；'
                '认识卡等系统条目由系统维护，不能替换）。写入成功才可对主人回显「记下了」。',
    parameters={'type': 'object', 'properties': {
        'kind': {'type': 'string', 'enum': ['episode', 'owner_event', 'habit_candidate', 'session_takeaway']},
        'summary': {'type': 'string', 'description': '一句话人类可读表达（≤300字），是检索与认识卡蒸馏的原料'},
        'subject': {'type': 'string', 'enum': ['pet', 'owner'], 'description': '这段经历属于谁，默认 pet'},
        'startAt': {'type': 'string', 'description': '发生开始时间（ISO，带时区），未知不传'},
        'endAt': {'type': 'string', 'description': '发生结束时间（ISO，带时区），可选'},
        'facts': {'type': 'object', 'description': '可选结构化字段（地点/对象/事件名等）'},
        'tentative': {'type': 'boolean', 'description': '是否为不确定的规律候选，仅 habit_candidate 可为 true'},
        'supersedesId': {'type': 'integer', 'description': '纠正旧记忆时被替换条目的 id，须与新条目同类型'}},
        'required': ['kind', 'summary'], 'additionalProperties': False},
    group=MEMORY, forms=(DIALOGUE,), execute=_execute_write_memory))


def _execute_search_memories(args, context):
    kinds = args.get('kinds')
    if kinds is not None and (not isinstance(kinds, list)
                              or not all(isinstance(k, str) for k in kinds)):
        return {'error': 'kinds 应为字符串数组', 'memories': []}
    from_dt = pet_memory.parse_time(args.get('from'))
    to_dt = pet_memory.parse_time(args.get('to'))
    if args.get('from') and from_dt is None:
        return {'error': 'from 不是合法的 ISO 时间', 'memories': []}
    if args.get('to') and to_dt is None:
        return {'error': 'to 不是合法的 ISO 时间', 'memories': []}
    raw_limit = args.get('limit')
    limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 10
    with session_scope() as s:
        pet = _pet_of(s, context)
        return pet_memory.search(s, pet.id, kinds=kinds, from_dt=from_dt, to_dt=to_dt,
                                 keyword=args.get('keyword'), limit=limit)


register(ToolSpec(
    name='search_memories',
    description='检索本宠的长期记忆（主人讲述过的经历、规律候选、会话要点、日/周汇总）。'
                '支持按类型（kinds）与发生时间窗口（from/to，含起点、不含终点）过滤，'
                'keyword 按原文子串匹配 summary（用主人原话里的关键词，如「公园」「叫声」，'
                '3 个字以上效果最好）。主人问「上次/之前说过的事」必须先用本工具查证，'
                '检索不到就如实说没有记录，不得编造记忆。',
    parameters={'type': 'object', 'properties': {
        'kinds': {'type': 'array', 'items': {'type': 'string',
                  'enum': list(pet_memory.KINDS)}, 'description': '类型过滤，可选'},
        'from': {'type': 'string', 'description': '发生时间下界（ISO，带时区）'},
        'to': {'type': 'string', 'description': '发生时间上界（ISO，带时区）'},
        'keyword': {'type': 'string', 'description': '关键词（主人原话的关键词）'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30, 'description': '默认10'}},
        'additionalProperties': False},
    group=MEMORY, forms=(DIALOGUE,), execute=_execute_search_memories))


def _execute_forget_memory(args, context):
    memory_id = args.get('memoryId')
    if not isinstance(memory_id, int) or isinstance(memory_id, bool):
        return {'forgotten': False, 'error': '请提供要忘掉的记忆 id（memoryId，整数）'}
    with session_scope() as s:
        pet = _pet_of(s, context)
        row, error = pet_memory.forget(s, pet.id, memory_id)
        if error:
            return {'forgotten': False, 'error': error}
        try:
            pet_memory.write_report(s, pet)  # 导出即时一致；写失败由 worker 下个 tick 补写
        except OSError:
            logging.getLogger(__name__).warning('memory report rewrite failed for pet %s', pet.id)
        return {'forgotten': True, 'memoryId': row.id, 'status': row.status,
                'note': '条目保留可审计；后续不得再引用这条记忆回答主人'}


register(ToolSpec(
    name='forget_memory',
    description='忘掉一条长期记忆（仅在主人明确要求「忘掉/删掉刚才记的/别记这个」时使用）。'
                'memoryId 来自 search_memories 的结果或本回合刚写入的返回。忘掉后如实确认，'
                '之后不再引用该条记忆。',
    parameters={'type': 'object', 'properties': {
        'memoryId': {'type': 'integer', 'description': '要忘掉的记忆条目 id'}},
        'required': ['memoryId'], 'additionalProperties': False},
    group=MEMORY, forms=(DIALOGUE,), execute=_execute_forget_memory))


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
    group=FACT_READ, forms=(DIALOGUE, REVIEW, EVENT), execute=_execute_read_pet_profile))

def _execute_read_care_topics(args, context):
    raw_status = args.get('status')
    status = raw_status if raw_status in ('open', 'ongoing', 'recovered', 'closed') else None
    raw_limit = args.get('limit')
    limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 10
    with session_scope() as s:
        pet = _pet_of(s, context)
        from petsense.agent import care
        stmt = select(care.AgentCareTopic).where(care.AgentCareTopic.pet_id == pet.id)
        if status:
            stmt = stmt.where(care.AgentCareTopic.status == status)
        rows = s.execute(stmt.order_by(care.AgentCareTopic.updated_at.desc())
                         .limit(min(20, max(1, limit)))).scalars().all()
        return {'topics': [care.public(row) for row in rows],
                'note': '主动提醒主题与依据；数值与时间来自依据列表，转述时如实带时间，不得外推'}


register(ToolSpec(
    name='read_care_topics',
    description='查看本宠最近的主动提醒主题（设备事实、周期发现、学习完成）及其依据：'
                '类别、状态（打开/持续/恢复）、严重度、发生与更新时间、依据帧时间与数值。'
                '主人问「为什么提醒我」或想了解某条提醒的来龙去脉时先用本工具查证，'
                '按依据如实回答，检索不到就说明没有对应记录，不得编造。',
    parameters={'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['open', 'ongoing', 'recovered', 'closed'],
                   'description': '状态过滤，可选'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'description': '默认10'}},
        'additionalProperties': False},
    group=FACT_READ, forms=(DIALOGUE,), execute=_execute_read_care_topics))


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


def _execute_read_web_page(args, context):
    url = args.get('url')
    if not isinstance(url, str) or not url.strip():
        return {'error': '请提供要读取的网页地址（http/https 绝对地址）。'}
    try:
        page = fetch.fetch_page(url.strip())
    except fetch.FetchError as exc:
        # 失败语义（W06 spec §4.4）：明确原因回给模型，运行继续；不得当作「来源不存在」
        return {'error': f'网页读取失败：{exc}。请换一个来源或基于已有摘要如实回答，'
                         '不得当作来源不存在，也不得声称已读取本页内容。'}
    return {'url': page['url'], 'title': page['title'], 'text': page['text'],
            'truncated': page['truncated'],
            'note': '正文已提取，超过限制会被截断；truncated=true 时内容不完整，引用时如实说明'}


register(ToolSpec(
    name='read_web_page',
    description='深读一个公网网页的正文：用于 web_search 来源摘要不足、需要细节或原文口径时，'
                '或主人明确给出链接时；浅层信息优先用搜索摘要，不要为常识问题逐页阅读。'
                '仅支持 http/https 公网页面（80/443 端口），内网地址与代理一律拒绝，页面最大 2MB、'
                '正文截断 4000 字符。失败会返回明确原因（不存在/超时/非网页/超限等），不得当作来源不存在。',
    parameters={'type': 'object', 'properties': {
        'url': {'type': 'string', 'description': '要读取的网页地址（http/https 绝对地址）'}},
        'required': ['url'], 'additionalProperties': False},
    group=EXTERNAL_READ, forms=(DIALOGUE,), execute=_execute_read_web_page, timeout_s=20.0))


def _execute_control_device(args, context):
    return actions_service.prepare_recording(args, context)


register(ToolSpec(
    name='control_device',
    description='让本宠的项圈远程录一段环境音（仅支持录音，durationSec 为 1—10 整数秒）。'
                '调用只生成录音确认卡（含目标项圈与时长），必须主人在卡片上确认后才真正向项圈'
                '发送指令——未经确认不得声称已发送指令或已录音。指令发送与音频回传分开显示，'
                '执行状态以动作卡为准；失败、超时或结果未知都会如实标注，结果未知时不自动重试、'
                '需再次确认新候选。只在主人明确想听项圈现场声音时发起。',
    parameters={'type': 'object', 'properties': {
        'durationSec': {'type': 'integer', 'minimum': 1, 'maximum': 10,
                        'description': '录音时长（秒），1—10 的整数'},
        'note': {'type': 'string', 'description': '发起理由（入运行轨迹）'}},
        'required': ['durationSec'], 'additionalProperties': False},
    group=DEVICE_CONTROL, forms=(DIALOGUE,), execute=_execute_control_device))


def _execute_create_diary(args, context):
    return actions_service.prepare_diary(args, context)


register(ToolSpec(
    name='create_diary',
    description='生成或读取本宠今天的 AI 运动海报（PetGIF 运动日记）。op=read 查看今天已有任务与'
                '状态，可直接执行；op=generate 发起首次生成或重新生成——需要先在确认卡上向主人'
                '说明：会使用宠物档案照片、照片将发送给 PetGIF 服务、海报链接生成后可被公开访问。'
                '生成需档案照片与今天至少 1 分钟有效走动；步数是按试点步频的估算值，'
                '回复不得虚构步数或数据来源，任务真实完成后才呈现海报结果。',
    parameters={'type': 'object', 'properties': {
        'op': {'type': 'string', 'enum': ['read', 'generate'],
               'description': 'read=查看今天已有日记；generate=发起/重新生成（需确认卡）'},
        'note': {'type': 'string', 'description': '发起理由（入运行轨迹）'}},
        'required': ['op'], 'additionalProperties': False},
    group=ACTION, forms=(DIALOGUE,), execute=_execute_create_diary))


def _execute_draft_social_post(args, context):
    explicit = bool(args.get('explicit'))
    return actions_service.save_draft(args, context, explicit)


register(ToolSpec(
    name='draft_social_post',
    description='把社区动态文字保存为本宠的私有草稿（仅主人可见，发布页有独立草稿位，'
                '原有手写草稿不受影响）。主人明确要求保存/写下来时传 explicit=true 直接保存'
                '并如实回显草稿内容；主人只是商量或请你拟内容（意图模糊）时传 explicit=false '
                '先出确认卡。只保存草稿、没有任何发布动作，正式发布由主人在社区发布页手动完成，'
                '不得声称已发布。caption 1—500 字，location 可选且 ≤80 字。',
    parameters={'type': 'object', 'properties': {
        'caption': {'type': 'string', 'description': '动态正文（1—500 字）'},
        'location': {'type': 'string', 'description': '地点（可选，≤80 字）'},
        'explicit': {'type': 'boolean', 'description': '主人是否明确要求保存'},
        'note': {'type': 'string', 'description': '发起理由（入运行轨迹）'}},
        'required': ['caption', 'explicit'], 'additionalProperties': False},
    group=ACTION, forms=(DIALOGUE,), execute=_execute_draft_social_post))


def _execute_read_task(args, context):
    task_id = args.get('taskId')
    if task_id is not None and (isinstance(task_id, bool) or not isinstance(task_id, int)):
        return {'error': 'taskId 需为整数'}
    with session_scope() as s:
        pet = _pet_of(s, context)
        from petsense.agent import tasks as task_service
        return task_service.read_tool_payload(s, pet, task_id=task_id)


register(ToolSpec(
    name='read_task',
    description='查询本宠当前的观察委托与交付（事实观察/个人比较/定时回顾）：状态（观察中、'
                '等待基线、已暂停、已交付、已取消）、当前计划、截止时间、已发出的提醒与回信。'
                '传 taskId 看单个委托详情（含历史交付）；缺省列出全部。主人问「我托付过你什么/'
                '之前让你留意的事怎么样了」用本工具查证，检索不到就如实说没有，不得编造委托。',
    parameters={'type': 'object', 'properties': {
        'taskId': {'type': 'integer', 'description': '委托 id，缺省列全部'}},
        'additionalProperties': False},
    group=TASK, forms=(DIALOGUE,), execute=_execute_read_task))


def _execute_propose_task_change(args, context):
    from petsense.agent import proposals as proposal_service
    from petsense.agent import tasks as task_service
    try:
        payload = task_service.normalize_proposal_fields(args, now=utc_now())
    except task_service.TaskError as exc:
        return {'error': exc.message,
                'guidance': '委托候选未通过校验，未生成。请修正后重新发起，或如实向主人说明原因。'}
    if payload['action'] in ('update', 'renew', 'pause', 'resume', 'cancel'):
        with session_scope() as s:
            pet = _pet_of(s, context)
            task = s.get(task_service.AgentTask, payload['taskId'])
            if task is None or task.pet_id != pet.id:
                return {'error': '委托不存在或不在当前宠物名下，先用 read_task 查证。'}
            if task.current_version != payload['expectedVersion']:
                return {'error': '委托计划已更新，请用 read_task 读取最新版本后重试。',
                        'currentVersion': task.current_version}
    try:
        with session_scope() as s:
            pet = _pet_of(s, context)
            row, refreshed = proposal_service.create_task_proposal(
                s, pet, context.get('turn_id'), payload)
    except proposal_service.ProposalError as exc:
        return {'error': exc.message,
                'guidance': '候选未生成。请向主人如实说明原因。'}
    refreshed_ids = [tid for tid in refreshed if tid != context.get('turn_id')]
    action_note = {
        'create': '新建观察委托', 'update': '修改委托计划', 'renew': '续期委托',
        'pause': '暂停委托', 'resume': '恢复委托', 'cancel': '取消委托'}[payload['action']]
    return {'written': False, 'proposal': proposal_service.public(row),
            'refreshedTurnIds': refreshed_ids,
            'guidance': f'已生成「{action_note}」待确认候选（确认卡会出现在回复中）。'
                        '未经主人在确认卡上确认前，不得声称已开始观察、已记录或已生效；'
                        '主人要改候选时，按新内容重新调用本工具生成新版本。'}


register(ToolSpec(
    name='propose_task_change',
    description='生成或更新观察委托的待确认候选（确认卡）。主人请你「留意/盯着/到时告诉我/'
                '这周帮我看看」某件事时使用，action 按语义选择：create=新建、update=修改计划、'
                'renew=续期（截止后再延长）、pause/resume/cancel=暂停/恢复/取消既有委托'
                '（后四类须带 taskId 与 read_task 返回的 currentVersion）。'
                'plan 字段：kind=fact_watch（盯某指标是否越过阈值）/ comparison（与个人基线比较）/'
                'scheduled_review（到时汇总回顾）；question=主人的关注点原意；'
                'feature=actions（走动）或 sounds（声音事件）；condition={metric,op(at_least/at_most),'
                'value,scope(window/daily)}；comparison 类计划给 comparison.band（below=比平时少/'
                'above=比平时多）与 baselineStrategy（fact_first=先按事实观察/wait_baseline=等基线就绪）；'
                'startAt/deadlineAt 为 ISO 时间（期限缺省 3 天、最长 30 天；「这周」到本周日结束）。'
                '本工具只生成候选，确认后才开始观察；评估与回信由系统检查点完成。',
    parameters={'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['create', 'update', 'renew', 'pause', 'resume', 'cancel']},
        'plan': {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['fact_watch', 'comparison', 'scheduled_review']},
            'title': {'type': 'string', 'description': '委托短标题（≤24 字），可选'},
            'question': {'type': 'string', 'description': '主人关注点（≤200 字）'},
            'feature': {'type': 'string', 'enum': ['actions', 'sounds']},
            'condition': {'type': 'object', 'properties': {
                'metric': {'type': 'string', 'enum': ['walkSeconds', 'soundEventCount']},
                'op': {'type': 'string', 'enum': ['at_least', 'at_most']},
                'value': {'type': 'number'},
                'scope': {'type': 'string', 'enum': ['window', 'daily']}},
                'description': '阈值条件：value 秒数/次数，scope=window 自开始累计 / daily 按日'},
            'comparison': {'type': 'object', 'properties': {
                'band': {'type': 'string', 'enum': ['below', 'above']}}},
            'baselineStrategy': {'type': 'string', 'enum': ['fact_first', 'wait_baseline']},
            'timezone': {'type': 'string', 'description': '缺省 Asia/Shanghai'},
            'startAt': {'type': 'string', 'description': 'ISO 时间；缺省现在'},
            'deadlineAt': {'type': 'string', 'description': 'ISO 时间；缺省 3 天后'}},
            'required': ['kind', 'question']},
        'taskId': {'type': 'integer', 'description': '目标委托 id（update/renew/pause/resume/cancel 必填）'},
        'expectedVersion': {'type': 'integer', 'description': 'read_task 返回的 currentVersion'},
        'note': {'type': 'string', 'description': '提案理由（入运行轨迹）'}},
        'required': ['action'], 'additionalProperties': False},
    group=TASK, forms=(DIALOGUE,), execute=_execute_propose_task_change))


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
            result = pool.submit(spec.execute, arguments, context).result(timeout=spec.timeout_s)
    except FutureTimeout:
        return {'allowed': True, 'reason': 'timeout', 'duration_ms': int((time.monotonic() - started) * 1000),
                'result': {'error': f'工具执行超时（>{spec.timeout_s:.0f}s）。请如实向主人说明未能查到，不得当作没有记录。'}}
    except Exception as exc:
        return {'allowed': True, 'reason': 'error', 'duration_ms': int((time.monotonic() - started) * 1000),
                'result': {'error': f'工具执行失败（{type(exc).__name__}）。请如实向主人说明查询失败，不得当作没有记录。'}}
    return {'allowed': True, 'reason': None, 'duration_ms': int((time.monotonic() - started) * 1000),
            'result': result}


def tool_names():
    return sorted(_TOOLS)
