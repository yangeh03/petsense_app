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
from datetime import datetime
from zoneinfo import ZoneInfo

from petsense.agent.observations import query as observations_query
from petsense.db import session_scope
from petsense.db.models import Pet

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
                'result': {'error': '请先在回复正文首行输出单行 JSON 范围声明 '
                                    '({"scope":"allowed|out_of_scope","intent":"...","speaker":"pet|assistant"})，'
                                    '然后再调用工具。'}, }
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
