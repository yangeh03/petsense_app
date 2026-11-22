"""MiniMax Responses API 只读冒烟（W01 spec §8 步骤 0，agent-w01-spec.md）。

D1 已确认主对话整体迁 /v1/responses；W00 只实测过该端点的 web_search，
本脚本补测主对话命脉：基础补全、system/instructions 形态、参数形状、
流式增量事件、Function Calling 往返与 usage。只读：不触数据库、不写文件。

用法：
  本地：MINIMAX_API_KEY=... python3 minimax_responses_smoke.py
  云端：python3 - < minimax_responses_smoke.py（凭据环境与 petsense.service 一致）

退出码只反映 R0 凭据/端点健康；其余项结论是输出数据。
"""
import json
import os
import sys
from pathlib import Path

import requests

TIMEOUT = (10, 60)
EVIDENCE_LIMIT = 400


def load_config():
    key = os.environ.get('MINIMAX_API_KEY')
    if not key and os.environ.get('MINIMAX_API_KEY_FILE'):
        key = Path(os.environ['MINIMAX_API_KEY_FILE']).read_text().strip()
    if not key:
        raise SystemExit('未配置 MiniMax 凭据（MINIMAX_API_KEY 或 MINIMAX_API_KEY_FILE）')
    return key, os.environ.get('MINIMAX_BASE_URL', 'https://api.minimaxi.com/v1').rstrip('/'), \
        os.environ.get('MINIMAX_MODEL', 'MiniMax-M3.1-Flash')


def post(key, base, payload, stream=False):
    try:
        response = requests.post(base + '/responses', headers={'Authorization': 'Bearer ' + key},
                                 json=payload, timeout=TIMEOUT, stream=stream)
    except requests.RequestException as exc:
        return None, f'{type(exc).__name__}: {exc}'
    if stream:
        return response.status_code, response
    text = response.text
    try:
        return response.status_code, response.json() if text else ''
    except ValueError:
        return response.status_code, text


def evidence(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:EVIDENCE_LIMIT].replace('\n', ' ')


def output_text(body):
    """从 Responses 输出结构提取正文：兼容 output[].content[].output_text、字符串 content 与顶层 output_text。"""
    if not isinstance(body, dict):
        return ''
    if isinstance(body.get('output_text'), str) and body['output_text']:
        return body['output_text']
    parts = []

    def take(content):
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') in ('output_text', 'text') and block.get('text'):
                    parts.append(block['text'])
    for item in body.get('output') or []:
        if not isinstance(item, dict):
            continue
        if item.get('type') in ('message', None):
            take(item.get('content'))
        elif item.get('type') == 'output_text' and item.get('text'):
            parts.append(item['text'])
    return ''.join(parts)


def function_calls(body):
    """收集 output 中的 function_call 项：(name, arguments_json_str, call_id)。"""
    calls = []
    if not isinstance(body, dict):
        return calls
    for item in body.get('output') or []:
        if isinstance(item, dict) and item.get('type') == 'function_call' and item.get('name'):
            calls.append((item['name'], item.get('arguments') or '', item.get('call_id') or item.get('id') or ''))
    return calls


def main():
    key, base, model = load_config()
    print(f'# MiniMax Responses 冒烟 base={base} model={model}')

    status, body = post(key, base, {'model': model, 'input': [
        {'role': 'user', 'content': '只回复四个字：连通正常'}]})
    text = output_text(body) if status == 200 else ''
    ok = status == 200 and '连通' in text
    print(f'[R0] 基础补全 input=messages -> {"pass" if ok else "FAIL"} (HTTP {status}): {evidence(text or body)[:160]}')
    if not ok:
        print('结论：凭据/端点不可用，后续项全部 skip。')
        return 1

    # R0b system 形态：input 内 role=system vs 顶层 instructions
    sys_msg = '你只能说「好」。'
    s1, b1 = post(key, base, {'model': model, 'input': [
        {'role': 'system', 'content': sys_msg}, {'role': 'user', 'content': '讲个笑话'}]})
    v1 = 'accepted' if s1 == 200 else f'http-{s1}'
    s2, b2 = post(key, base, {'model': model, 'instructions': sys_msg,
                              'input': [{'role': 'user', 'content': '讲个笑话'}]})
    v2 = 'accepted' if s2 == 200 else f'http-{s2}'
    print(f'[R0b] system 形态 -> input-system: {v1}; instructions: {v2}'
          f'（正文示例: {evidence(output_text(b1) or b1)[:80]}）')

    # R1 参数形状：max 输出与推理力度
    for field, value in (('max_output_tokens', 100), ('max_tokens', 100),
                         ('reasoning_effort', 'low'), ('reasoning', {'effort': 'low'})):
        payload = {'model': model, 'input': [{'role': 'user', 'content': '只回复两个字：收到'}], field: value}
        s, b = post(key, base, payload)
        verdict = 'accepted' if s == 200 else f'rejected(http-{s})'
        print(f'[R1] {field}={value!r} -> {verdict}: {evidence(output_text(b) or b)[:100]}')

    # R2 流式：收集事件类型并拼装增量
    s, response = post(key, base, {'model': model, 'stream': True, 'input': [
        {'role': 'user', 'content': '用一句话介绍你自己'}]}, stream=True)
    event_types, deltas, usage_frame = [], [], None
    if s == 200:
        current_event = None
        for raw in response.iter_lines():
            if not raw:
                continue
            line = raw.decode('utf-8', 'replace')
            if line.startswith('event:'):
                current_event = line[6:].strip()
                continue
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            etype = current_event or obj.get('type') or ''
            if etype and etype not in event_types:
                event_types.append(etype)
            if etype.endswith('output_text.delta') and isinstance(obj.get('delta'), str):
                deltas.append(obj['delta'])
            if 'usage' in obj and isinstance(obj.get('usage'), dict):
                usage_frame = etype or 'inline'
    stream_ok = s == 200 and ''.join(deltas).strip() and any('delta' in t for t in event_types)
    print(f'[R2] 流式 -> {"pass" if stream_ok else "FAIL"} (HTTP {s}) events={event_types[:12]}'
          f' 拼装={len("".join(deltas))}字 usage@{usage_frame}')
    print(f'     增量示例: {evidence("".join(deltas))[:100]}')

    # R3 Function Calling 首往返
    tool = {'type': 'function', 'name': 'query_observations',
            'description': '查询宠物项圈在指定时间窗口的真实观测数据',
            'parameters': {'type': 'object', 'properties': {
                'start': {'type': 'string', 'description': '起始时间，ISO带时区'},
                'end': {'type': 'string', 'description': '结束时间，ISO带时区，不含'}},
                'required': ['start', 'end'], 'additionalProperties': False}}
    s3, b3 = post(key, base, {'model': model, 'input': [
        {'role': 'system', 'content': '你是宠物助手。需要数据时必须调用工具，不得编造。'},
        {'role': 'user', 'content': '它昨天走动多吗？今天是2026-11-22，时区Asia/Shanghai。请查询后回答。'}],
        'tools': [tool]})
    calls = function_calls(b3) if s3 == 200 else []
    fc_ok = s3 == 200 and bool(calls)
    print(f'[R3] Function Calling -> {"pass" if fc_ok else "FAIL"} (HTTP {s3}) calls={[(c[0], c[2]) for c in calls]}'
          f' args={evidence(calls[0][1]) if calls else evidence(b3)[:150]}')

    # R4 Function Calling 回传结果闭环（V1 形态：全量回显含 system；结果走 output 字段）
    roundtrip_ok, roundtrip_text = False, ''
    if calls:
        name, args, call_id = calls[0]
        user_q = '它昨天走动多吗？今天是2026-11-22，时区Asia/Shanghai。请查询后回答。'
        follow = {'model': model, 'input': [
            {'role': 'system', 'content': '你是宠物助手。需要数据时调用工具，拿到工具结果后必须直接作答，不得重复调用。'},
            {'role': 'user', 'content': user_q},
            {'type': 'function_call', 'call_id': call_id, 'name': name, 'arguments': args},
            {'type': 'function_call_output', 'call_id': call_id,
             'output': json.dumps({'observedSeconds': 3600, 'actionSeconds': {'走动': 5400}}, ensure_ascii=False)}],
            'tools': [tool]}
        s4, b4 = post(key, base, follow)
        roundtrip_text = output_text(b4) if s4 == 200 else ''
        used = any(w in roundtrip_text for w in ('5400', '5,400', '1.5', '90', '九十', '一个半小时'))
        roundtrip_ok = s4 == 200 and bool(roundtrip_text.strip()) and used
        print(f'[R4] 工具结果回传(V1全量回显) -> {"pass" if roundtrip_ok else "FAIL"} (HTTP {s4}): {evidence(roundtrip_text or b4)[:160]}')
        if isinstance(b4, dict) and b4.get('usage'):
            print(f'     usage: {evidence(b4["usage"])}')

    print('\n== 结论矩阵（回填 agent-w01-spec.md §8 步骤 0）==')
    print(f'R0 基础补全: {"available" if ok else "unavailable"}')
    print(f'R0b system: input-system={v1}; instructions={v2}')
    print('R1 参数形状: 见上方逐项（accepted=HTTP200，语义是否生效需行为确认）')
    print(f'R2 流式增量: {"available" if stream_ok else "unavailable"}')
    print(f'R3 FC 往返: {"available" if fc_ok else "unavailable"}')
    if calls:
        print(f'R4 结果回传: {"available" if roundtrip_ok else "unavailable"}')
    print('主对话迁移判定: ' + ('可行' if (ok and stream_ok and fc_ok and (roundtrip_ok or not calls)) else '存在阻断项，按 spec 停下来向负责人澄清'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
