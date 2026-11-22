"""MiniMax Responses API 适配层（W01，agent-w01-spec.md §3.1）。

只负责传输与解析：凭据加载、/v1/responses 请求、SSE 事件流解析（输出文本增量、
函数调用、usage）。循环编排在 loop.py；本文件不做任何业务判断。

实测约束（2026-11-22 冒烟，tools/minimax_responses_smoke.py）：
- 会话是无状态全量回显：input 需含 system + user + 模型的 function_call 项 +
  function_call_output（结果放 output 字段；content 字段会被静默忽略）；
- previous_response_id 链式调用 400 不可用；
- 流式事件为标准 OpenAI Responses 形态（response.output_text.delta /
  response.function_call_arguments.delta / response.completed）。
"""
import json
import os
from pathlib import Path

import requests

REQUEST_TIMEOUT = (10, 60)


def load_credentials():
    key = os.environ.get('MINIMAX_API_KEY')
    if not key and os.environ.get('MINIMAX_API_KEY_FILE'):
        key = Path(os.environ['MINIMAX_API_KEY_FILE']).read_text().strip()
    if not key:
        raise ValueError('尚未配置 MiniMax')
    base = os.environ.get('MINIMAX_BASE_URL', 'https://api.minimaxi.com/v1').rstrip('/')
    model = os.environ.get('MINIMAX_MODEL', 'MiniMax-M3.1-Flash')
    return key, base, model


def _text_of(response):
    """提取 message 项正文；兼容 content 为块列表或字符串两种形态。"""
    parts = []

    def take(content):
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') in ('output_text', 'text') and block.get('text'):
                    parts.append(block['text'])

    for item in response.get('output') or []:
        if isinstance(item, dict) and item.get('type') in ('message', None):
            take(item.get('content'))
    return ''.join(parts)


def _calls_of(response):
    calls = []
    for item in response.get('output') or []:
        if isinstance(item, dict) and item.get('type') == 'function_call' and item.get('name'):
            calls.append({'name': item['name'], 'arguments': item.get('arguments') or '',
                          'call_id': item.get('call_id') or item.get('id') or ''})
    return calls


def respond(input_items, tools=None, on_delta=None, max_output_tokens=1800):
    """一次模型往返。返回 {'text', 'function_calls', 'usage'}。

    input_items 为全量回显会话（role 字典与 function_call / function_call_output 项混排）。
    on_delta(cumulative_text) 随 output_text 增量回调（累计值，含首行 stance，剥离由调用方做）。
    """
    key, base, model = load_credentials()
    payload = {'model': model, 'input': input_items, 'stream': True,
               'reasoning_effort': 'low', 'max_output_tokens': max_output_tokens}
    if tools:
        payload['tools'] = tools
    with requests.post(base + '/responses', headers={'Authorization': 'Bearer ' + key},
                       json=payload, timeout=REQUEST_TIMEOUT, stream=True) as response:
        if response.status_code != 200:
            raise ValueError(f'MiniMax HTTP {response.status_code}: {response.text[:300]}')
        text_parts = []
        call_order = []
        current_call = None
        usage = {}
        final = None
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
                item = json.loads(data)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            kind = current_event or item.get('type') or ''
            if kind == 'response.completed' and isinstance(item.get('response'), dict):
                final = item['response']
            if kind == 'response.failed':
                raise ValueError(f'MiniMax response.failed: {json.dumps(item, ensure_ascii=False)[:300]}')
            if kind.endswith('output_text.delta') and isinstance(item.get('delta'), str):
                text_parts.append(item['delta'])
                if on_delta:
                    on_delta(''.join(text_parts))
            if kind.endswith('output_item.added'):
                added = item.get('item')
                if isinstance(added, dict) and added.get('type') == 'function_call':
                    current_call = {'name': added.get('name') or '',
                                    'call_id': added.get('call_id') or added.get('id') or '',
                                    'arguments': ''}
                    call_order.append(current_call)
            elif kind.endswith('function_call_arguments.delta') and current_call is not None:
                current_call['arguments'] += item.get('delta') or ''
        if final is not None:
            usage = final.get('usage') or {}
            return {'text': _text_of(final), 'function_calls': _calls_of(final), 'usage': usage}
        return {'text': ''.join(text_parts), 'function_calls': call_order, 'usage': usage}
