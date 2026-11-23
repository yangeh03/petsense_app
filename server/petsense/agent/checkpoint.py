"""W08 检查点模型运行（agent-w08-spec.md §3）：复用 W01 循环语义与运行轨迹。

检查点是 Agent 的后台运行形态：输入为已物化证据（观测、覆盖、缺口、计划版本、
可选基线版本），工具能力仅含事实读取（query_observations / read_baseline，权限矩阵
按 checkpoint 形态放行；设备、外部搜索、资料写入、委托变更与记忆写入一律拒绝），
输出为受约束的结构化决策（continue / remind / letter），由 tasks._commit_phase 用
物化证据校验可交付性后才落库。模型超时与格式不合法不抛异常：返回 decision=None
与结构化 error，由调用方按 D5/D6 走有界重试与降级回信。轨迹完整落 AgentRun
（form=checkpoint，agent_replay.py 可回放）。
"""
import json
import logging

from petsense.agent import model, registry, runs
from petsense.db import iso_z

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 2
MAX_PARSE_RETRIES = 1
MAX_OUTPUT_TOKENS = 1400
EVIDENCE_CHAR_LIMIT = 6000

_CHECKPOINT_SYSTEM = (
    '你是宠物照料助手的后台检查点，负责评估一条主人确认过的观察委托。'
    '只依据下方给定的物化证据（观测总量、逐日、覆盖、缺口、个人基线）作判断，'
    '可用观测查询工具补充核对窗口事实；不得编造数值，不得使用证据之外的记忆或联想。'
    '覆盖不足、缺口、无数据必须如实说明：无数据不等于零活动，缺口不能推断为正常或异常。'
    '输出要求：单独输出一个 JSON 对象，不要 markdown 代码块、不要 JSON 以外的文字：\n'
    '{"decision":"continue或remind或letter","text":"…"}\n'
    'decision 含义：continue=继续观察（普通检查且不值得打扰时）；'
    'remind=值得现在提醒主人（只在证据支撑计划条件或明显异常事实时使用，'
    'text ≤120 字，写明触发事实与数值）；letter=截止回信（仅当 checkType=due 时允许，'
    'text ≤300 字：先给结论或「无法下结论」，再给关键数值、实际覆盖与缺口，'
    '语气像照料助手写给主人的一段话，不用列表，不展示字段名）。'
    '个人比较必须引用证据中的基线范围与版本；基线未就绪或不可比时如实说明，'
    '只给事实观察。评估不修改计划本身。')


def _compact_evidence(evidence):
    """证据瘦身：总量 + 最近 7 日 + 相应基线维度（提示词有界）。"""
    compact = {key: evidence.get(key) for key in (
        'windowStart', 'windowEnd', 'observedSeconds', 'gapSeconds', 'pausedSeconds',
        'walkSeconds', 'soundEventCount', 'dataThrough', 'comparisonStarted')}
    compact['actionSeconds'] = evidence.get('actionSeconds')
    compact['daily'] = (evidence.get('daily') or [])[-7:]
    baseline = evidence.get('baseline')
    if isinstance(baseline, dict) and not baseline.get('error'):
        dims = []
        for dim in baseline.get('dimensions') or []:
            day = dim.get('day') or {}
            dims.append({'feature': dim.get('feature'), 'state': dim.get('state'),
                         'comparable': day.get('ready') and (day.get('target') or {}).get('comparable'),
                         'version': dim.get('version'), 'dayValue': (day.get('target') or {}).get('value'),
                         'dayRange': day.get('range'),
                         'dayNote': (day.get('target') or {}).get('note')})
        compact['baseline'] = {'date': baseline.get('date'), 'dimensions': dims}
    else:
        compact['baseline'] = baseline
    return compact


def _parse_decision(text):
    """解析模型输出为决策对象；不合法返回 None。"""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith('```'):
        stripped = stripped.strip('`')
        stripped = stripped[4:] if stripped[:4].lower() == 'json' else stripped
    start, end = stripped.find('{'), stripped.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(stripped[start:end + 1])
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get('decision') not in ('continue', 'remind', 'letter'):
        return None
    text_value = value.get('text')
    if value['decision'] in ('remind', 'letter') and (not isinstance(text_value, str)
                                                      or not text_value.strip()):
        return None
    return {'decision': value['decision'],
            'text': text_value.strip()[:2000] if isinstance(text_value, str) else ''}


def evaluate(ctx, now):
    """执行一次检查点模型评估。永不因模型问题抛异常。

    返回 {'decision': 'continue'|'remind'|'letter'|None, 'text', 'run_id', 'error'}；
    error ∈ {'model_unavailable', 'model_invalid_output', None}。
    """
    plan, evidence = ctx['plan'], ctx['evidence']
    compact = _compact_evidence(evidence)
    evidence_json = json.dumps(compact, ensure_ascii=False, default=str)
    if len(evidence_json) > EVIDENCE_CHAR_LIMIT:
        evidence_json = evidence_json[:EVIDENCE_CHAR_LIMIT] + '…(截断)'
    user_prompt = (f"委托计划：{json.dumps(plan, ensure_ascii=False)}\n"
                   f"检查类型：{'due（截止回信，必须给 letter）' if ctx['mode'] == 'due' else 'ordinary（普通检查）'}\n"
                   f"任务状态：{ctx['status']}\n当前时间：{iso_z(now)}\n"
                   f"物化证据：{evidence_json}")
    input_items = [{'role': 'system', 'content': _CHECKPOINT_SYSTEM},
                   {'role': 'user', 'content': user_prompt}]
    context = {'pet_id': ctx['pet_id'], 'user_id': ctx['user_id']}
    schemas = registry.schemas_for(registry.CHECKPOINT, 'allowed')
    tool_summary, messages_trace = [], [{'role': 'user', 'prompt': user_prompt[:800]}]
    rounds, parse_failures = 0, 0
    decision, error = None, None
    try:
        while True:
            allow_tools = rounds < MAX_TOOL_ROUNDS
            turn = model.respond(input_items, tools=schemas or None,
                                 max_output_tokens=MAX_OUTPUT_TOKENS)
            calls = (turn.get('function_calls') or [])[:3]
            if calls and allow_tools:
                rounds += 1
                for call in calls:
                    name, call_id = call['name'], call['call_id'] or f'call_{len(tool_summary)}'
                    try:
                        arguments = json.loads(call['arguments'] or '{}')
                    except ValueError:
                        arguments = None
                    gate = registry.execute_tool(name, arguments, registry.CHECKPOINT,
                                                 'allowed', context)
                    result_json = json.dumps(gate['result'], ensure_ascii=False, default=str)
                    if len(result_json) > 4096:
                        result_json = result_json[:4096] + '…(截断)'
                    input_items.append({'type': 'function_call', 'call_id': call_id,
                                        'name': name, 'arguments': call['arguments'] or '{}'})
                    input_items.append({'type': 'function_call_output', 'call_id': call_id,
                                        'output': result_json})
                    tool_summary.append({'name': name, 'allowed': gate['allowed'],
                                         'reason': gate['reason'],
                                         'arguments': (call['arguments'] or '')[:300],
                                         'duration_ms': gate['duration_ms'],
                                         'result_bytes': len(result_json)})
                messages_trace.append({'tool_round': rounds, 'calls': tool_summary[-len(calls):]})
                continue
            decision = _parse_decision(turn.get('text'))
            messages_trace.append({'role': 'assistant', 'text': (turn.get('text') or '')[:800]})
            if decision is None and parse_failures < MAX_PARSE_RETRIES:
                parse_failures += 1
                input_items.append({'role': 'assistant', 'content': turn.get('text') or ''})
                input_items.append({'role': 'user', 'content':
                                    '上一次输出不是合法的决策 JSON。重新输出：只输出一个 JSON 对象 '
                                    '{"decision":"continue|remind|letter","text":"…"}，不要其他文字。'
                                    + ('本次是截止回信（due），decision 必须为 letter。' if ctx['mode'] == 'due' else '')})
                continue
            if decision is not None and ctx['mode'] == 'ordinary' and decision['decision'] == 'letter':
                # 普通检查不交付回信（spec §3：到期才回信），按继续观察记录
                messages_trace.append({'coerced': 'letter_at_ordinary→continue'})
                decision = {'decision': 'continue', 'text': ''}
            if decision is not None and ctx['mode'] == 'due' and decision['decision'] != 'letter':
                decision = None  # due 必须 letter
            break
    except Exception as exc:  # noqa: BLE001  模型失败降级为结构化 error（D6）
        logger.warning('checkpoint model failed for task %s: %s', ctx.get('task_id'),
                       type(exc).__name__)
        messages_trace.append({'model_error': f'{type(exc).__name__}: {str(exc)[:200]}'})
        error = 'model_unavailable'
    if decision is None and error is None:
        error = 'model_invalid_output'
    degraded = [error] if error else []
    trace = {'form': 'checkpoint',
             'trigger': {'kind': 'task', 'taskId': ctx.get('task_id'), 'version': ctx.get('version'),
                         'checkType': ctx['mode'], 'taskStatus': ctx.get('status')},
             'conclusion': {'decision': (decision or {}).get('decision'),
                            'error': error, 'taskId': ctx.get('task_id'),
                            'version': ctx.get('version')},
             'messages': messages_trace, 'tool_summary': tool_summary,
             'budget': {'rounds': rounds, 'parse_failures': parse_failures},
             'degraded': degraded}
    run_id = runs.save(ctx['pet_id'], None, trace, 'degraded' if degraded else 'complete')
    return {'decision': (decision or {}).get('decision'), 'text': (decision or {}).get('text') or '',
            'run_id': run_id, 'error': error}
