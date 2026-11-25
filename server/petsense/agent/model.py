"""模型传输适配层（W01 Responses API；W19 增加 Chat Completions 形态）。

只负责传输与解析：凭据加载、请求、SSE 事件流解析（输出文本增量、函数调用、
内建 web_search_call、url_citation、usage）。循环编排在 loop.py；
本文件不做任何业务判断。

**两种请求形态**（`MINIMAX_API_MODE`：`responses` 默认 / `chat`；未设置时按 base
含 moonshot 自动走 chat）：
- `responses`：MiniMax Responses API（`/v1/responses`），原有实现不变。
- `chat`：OpenAI Chat Completions（`/v1/chat/completions`）。Kimi 的 Responses 端点
  忽略 `thinking`（推理关不掉），Chat Completions 才支持 `thinking.type=disabled`
  （K2 系列），因此关思考必须走这个形态。形态差异只在传输层消化：输入仍是
  Responses 形态的全量回显会话，输出仍是统一返回字典，loop 无感。

Responses 端实测约束（2026-11-22 冒烟，tools/minimax_responses_smoke.py / minimax_w06_smoke.py；
2026-11-23 E35 归因探针补充）：
- 会话是无状态全量回显：input 需含 system + user + 模型的 function_call 项 +
  function_call_output（结果放 output 字段；content 字段会被静默忽略）；
- previous_response_id 链式调用 400 不可用；
- 流式事件为标准 OpenAI Responses 形态（response.output_text.delta /
  response.function_call_arguments.delta / response.completed）；
- 内建 web_search 以 {'type':'web_search'} 追加进 tools（与 function 混排可用），
  执行在供应商侧，final output 含 web_search_call 项、message annotations 含
  url_citation；流式下搜索阶段可 >60s 无字节 → 挂搜索的请求读超时提高到 110s；
- **url_citation 与 web_search_call 的工件不保证随 response.completed 到达**：
  带工具调用的搜索轮可能以 web_search_call status=in_progress 收尾（无 annotations），
  长搜索流可能以 response.incomplete 结束（无 completed、无 [DONE]）。因此：
  response.incomplete 与 completed 同样按 final 提取；url_citation 另从
  response.output_text.annotation.added 流事件累计兜底；web_search_call 另按
  流事件 item_id 计数兜底（事件不带 query，query 仅在 final output 中）。

Chat Completions 端实测约束（2026-11-25，Kimi kimi-k2.6 国内节点）：
- 流式为标准 OpenAI Chat 形态（choices[].delta.content / delta.tool_calls），
  无 [DONE] 之外的额外事件；思考关闭时 delta.reasoning_content 为空；
- 工具调用为标准 tool_calls（index/id/type/function.name/function.arguments），
  工具结果以 role=tool + tool_call_id 回传可闭环；
- **供应商内建 web_search 在该端不执行**（无 web_search_call、无 url_citation），
  联网搜索由本地 search_web 工具走供应商独立搜索接口实现（见 agent/search.py）；
- 图片输入为 content 数组的 image_url 块（data URL）。
"""
import json
import os
from pathlib import Path

import requests

REQUEST_TIMEOUT = (10, 60)
SEARCH_READ_TIMEOUT_S = 110.0
SEARCH_PROGRESS_HINT = '正在联网核实…'
CITATION_SNIPPET_LIMIT = 200
API_MODE_ENV = 'MINIMAX_API_MODE'            # 'responses'（默认）| 'chat'
THINKING_DISABLED_ENV = 'MINIMAX_THINKING_DISABLED'


def load_credentials():
    key = os.environ.get('MINIMAX_API_KEY')
    if not key and os.environ.get('MINIMAX_API_KEY_FILE'):
        key = Path(os.environ['MINIMAX_API_KEY_FILE']).read_text().strip()
    if not key:
        raise ValueError('尚未配置 MiniMax')
    base = os.environ.get('MINIMAX_BASE_URL', 'https://api.minimaxi.com/v1').rstrip('/')
    model = os.environ.get('MINIMAX_MODEL', 'MiniMax-M3.1-Flash')
    return key, base, model


def api_mode(base=None):
    """请求形态：显式环境变量优先；未设置时按 base 推断（Moonshot 走 chat）。"""
    mode = os.environ.get(API_MODE_ENV, '').strip().lower()
    if mode in ('responses', 'chat'):
        return mode
    if base is None:
        base = os.environ.get('MINIMAX_BASE_URL', '')
    return 'chat' if 'moonshot' in (base or '').lower() else 'responses'


def thinking_disabled():
    """是否在 chat 形态下发送 thinking.type=disabled（仅 K2 系列支持该参数）。"""
    return os.environ.get(THINKING_DISABLED_ENV, '').strip().lower() in ('1', 'true', 'yes')


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


def _search_calls_of(response):
    """提取 final output 中的内建 web_search_call（W06）：id/action/status。"""
    calls = []
    for item in response.get('output') or []:
        if not isinstance(item, dict) or item.get('type') != 'web_search_call':
            continue
        action = item.get('action') if isinstance(item.get('action'), dict) else {}
        calls.append({'id': item.get('id') or '', 'status': item.get('status') or '',
                      'query': action.get('query') or '', 'openPage': action.get('url') or '',
                      'actionType': action.get('type') or ''})
    return calls


def _citations_of(response):
    """提取 message annotations 中的 url_citation（title/url + ≤200 字符 content 摘要）。

    annotations 可能在 content 块级或 message 项级（供应商两种形态都出现过），两处都取。"""
    citations = []
    for item in response.get('output') or []:
        if not isinstance(item, dict) or item.get('type') not in ('message', None):
            continue
        notes = list(item.get('annotations') or [])
        content = item.get('content')
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    notes.extend(block.get('annotations') or [])
        for note in notes:
            if not isinstance(note, dict) or note.get('type') != 'url_citation':
                continue
            citation = note.get('url_citation') if isinstance(note.get('url_citation'), dict) else note
            url = citation.get('url') or ''
            if not url:
                continue
            citations.append({'title': citation.get('title') or '', 'url': url,
                              'snippet': (citation.get('content') or '')[:CITATION_SNIPPET_LIMIT]})
    return citations


def _chat_content(content):
    """Responses 形态的 content → Chat Completions 的 content（字符串或块数组）。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return '' if content is None else str(content)
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if kind in ('input_text', 'text'):
            parts.append({'type': 'text', 'text': block.get('text') or ''})
        elif kind in ('input_image', 'image_url'):
            url = block.get('image_url')
            if isinstance(url, dict):
                url = url.get('url')
            if isinstance(url, str) and url:
                parts.append({'type': 'image_url', 'image_url': {'url': url}})
    return parts or ''


def _chat_messages(input_items):
    """Responses 形态的全量回显会话 → Chat Completions messages。

    function_call 项回显为带 tool_calls 的 assistant 消息；
    function_call_output 项回显为 role=tool 消息（tool_call_id 对齐）。"""
    messages = []
    for item in input_items:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        if kind == 'function_call':
            call_id = item.get('call_id') or item.get('id') or ''
            messages.append({'role': 'assistant', 'content': None,
                             'tool_calls': [{'id': call_id, 'type': 'function',
                                             'function': {'name': item.get('name') or '',
                                                          'arguments': item.get('arguments') or '{}'}}]})
        elif kind == 'function_call_output':
            output = item.get('output')
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, default=str)
            messages.append({'role': 'tool', 'tool_call_id': item.get('call_id') or '',
                             'content': output})
        else:
            messages.append({'role': item.get('role') or 'user',
                             'content': _chat_content(item.get('content'))})
    return messages


def _chat_tools(tools):
    """工具 schema → Chat Completions 的 function 形态。"""
    converted = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get('type') == 'function' and isinstance(tool.get('function'), dict):
            converted.append(tool)
            continue
        converted.append({'type': 'function', 'function': {
            'name': tool.get('name') or '',
            'description': tool.get('description') or '',
            'parameters': tool.get('parameters') or {'type': 'object', 'properties': {}}}})
    return converted


def _respond_chat(key, base, model_name, input_items, tools, on_delta, max_output_tokens):
    """Chat Completions 形态的一次往返（W19）。

    返回结构与 Responses 形态一致：{'text','function_calls','web_search_calls','citations','usage'}。
    该形态没有供应商内建搜索：web_search_calls 恒为空，联网搜索由本地工具负责。"""
    payload = {'model': model_name, 'messages': _chat_messages(input_items),
               'stream': True, 'max_tokens': max_output_tokens}
    if thinking_disabled():
        payload['thinking'] = {'type': 'disabled'}
    chat_tools = _chat_tools(tools)
    if chat_tools:
        payload['tools'] = chat_tools
    text_parts = []
    call_entries = {}
    call_order = []
    usage = {}
    with requests.post(base + '/chat/completions', headers={'Authorization': 'Bearer ' + key},
                       json=payload, timeout=REQUEST_TIMEOUT, stream=True) as response:
        if response.status_code != 200:
            raise ValueError(f'模型 HTTP {response.status_code}: {response.text[:300]}')
        for raw in response.iter_lines():
            if not raw:
                continue
            line = raw.decode('utf-8', 'replace')
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
            if item.get('error'):
                raise ValueError(f'模型返回错误：{json.dumps(item["error"], ensure_ascii=False)[:300]}')
            if isinstance(item.get('usage'), dict):
                usage = item['usage']
            for choice in item.get('choices') or []:
                delta = choice.get('delta') or {}
                content = delta.get('content')
                if isinstance(content, str) and content:
                    text_parts.append(content)
                    if on_delta:
                        on_delta(''.join(text_parts))
                for call in delta.get('tool_calls') or []:
                    if not isinstance(call, dict):
                        continue
                    index = call.get('index')
                    key_index = index if isinstance(index, int) else len(call_order)
                    entry = call_entries.get(key_index)
                    if entry is None:
                        entry = {'id': '', 'name': '', 'arguments': ''}
                        call_entries[key_index] = entry
                        call_order.append(entry)
                    if call.get('id'):
                        entry['id'] = call['id']
                    function = call.get('function') or {}
                    if function.get('name'):
                        entry['name'] = function['name']
                    if function.get('arguments'):
                        entry['arguments'] += function['arguments']
    function_calls = [{'name': entry['name'], 'arguments': entry['arguments'],
                       'call_id': entry['id'] or f'call_{index}'}
                      for index, entry in enumerate(call_order) if entry['name']]
    return {'text': ''.join(text_parts), 'function_calls': function_calls,
            'web_search_calls': [], 'citations': [], 'usage': usage}


def respond(input_items, tools=None, on_delta=None, max_output_tokens=1800, web_search=False):
    """一次模型往返。返回 {'text', 'function_calls', 'web_search_calls', 'citations', 'usage'}。

    input_items 为全量回显会话（role 字典与 function_call / function_call_output 项混排）。
    on_delta(cumulative_text) 随 output_text 增量回调（累计值，含首行 stance，剥离由调用方做）。
    web_search=True 时追加内建 {'type':'web_search'} 工具并把读超时放宽到 110s；流式收到
    首个 web_search_call 事件时经 on_delta 推一次 SEARCH_PROGRESS_HINT（后继正文会覆盖它）。
    搜索工件按双通道回收（见模块 docstring）：final/completed 为主，response.incomplete
    同样按 final 提取；url_citation 流事件与 web_search_call item_id 作为截断流兜底，
    与 final 提取按 url/id 合并去重。
    """
    key, base, model = load_credentials()
    if api_mode(base) == 'chat':
        # chat 形态没有供应商内建搜索：web_search 参数在此形态被忽略，联网搜索走本地工具
        return _respond_chat(key, base, model, input_items, tools, on_delta, max_output_tokens)
    payload = {'model': model, 'input': input_items, 'stream': True,
               'reasoning_effort': 'low', 'max_output_tokens': max_output_tokens}
    if tools:
        payload['tools'] = tools
    if web_search:
        payload['tools'] = list(payload.get('tools') or []) + [{'type': 'web_search'}]
    timeout = (REQUEST_TIMEOUT[0], SEARCH_READ_TIMEOUT_S if web_search else REQUEST_TIMEOUT[1])
    with requests.post(base + '/responses', headers={'Authorization': 'Bearer ' + key},
                       json=payload, timeout=timeout, stream=True) as response:
        if response.status_code != 200:
            raise ValueError(f'MiniMax HTTP {response.status_code}: {response.text[:300]}')
        text_parts = []
        call_order = []
        current_call = None
        usage = {}
        final = None
        current_event = None
        search_hint_sent = False
        stream_citations = {}   # url -> citation，来自 annotation.added 流事件（截断流兜底）
        stream_search_ids = []  # web_search_call 流事件的 item_id（事件不带 query）
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
            if kind in ('response.completed', 'response.incomplete') and isinstance(item.get('response'), dict):
                # incomplete（如输出预算耗尽）也携带已产出的 output 与 usage，按 final 提取
                final = item['response']
            if kind == 'response.failed':
                raise ValueError(f'MiniMax response.failed: {json.dumps(item, ensure_ascii=False)[:300]}')
            if kind.endswith('output_text.delta') and isinstance(item.get('delta'), str):
                text_parts.append(item['delta'])
                if on_delta:
                    on_delta(''.join(text_parts))
            if kind.endswith('output_text.annotation.added'):
                citation = item.get('annotation')
                if isinstance(citation, dict) and citation.get('type') in ('url_citation', None):
                    resolved = citation.get('url_citation') if isinstance(citation.get('url_citation'), dict) else citation
                    url = resolved.get('url') or ''
                    if url and url not in stream_citations:
                        stream_citations[url] = {'title': resolved.get('title') or '', 'url': url,
                                                 'snippet': (resolved.get('content') or '')[:CITATION_SNIPPET_LIMIT]}
            if '.web_search_call.' in kind or kind.startswith('web_search_call.'):
                # 进度事件只带 item_id（无 query）；final 缺失时按 id 兜底计数
                event_item = item.get('item') if isinstance(item.get('item'), dict) else item
                item_id = event_item.get('item_id') or event_item.get('id') or ''
                if item_id and item_id not in stream_search_ids:
                    stream_search_ids.append(item_id)
            if not search_hint_sent and 'web_search_call' in kind and on_delta:
                # W06 §4.3：搜索期间的可见提示（后继真实正文覆盖；DB 节流写以最终值为准）
                search_hint_sent = True
                on_delta(SEARCH_PROGRESS_HINT)
            if kind.endswith('output_item.added'):
                added = item.get('item')
                if isinstance(added, dict) and added.get('type') == 'function_call':
                    current_call = {'name': added.get('name') or '',
                                    'call_id': added.get('call_id') or added.get('id') or '',
                                    'arguments': ''}
                    call_order.append(current_call)
                elif isinstance(added, dict) and added.get('type') == 'web_search_call':
                    item_id = added.get('id') or ''
                    if item_id and item_id not in stream_search_ids:
                        stream_search_ids.append(item_id)
            elif kind.endswith('function_call_arguments.delta') and current_call is not None:
                current_call['arguments'] += item.get('delta') or ''
        if final is not None:
            usage = final.get('usage') or {}
            search_calls = _search_calls_of(final)
            known_ids = {c['id'] for c in search_calls if c['id']}
            search_calls.extend({'id': item_id, 'status': '', 'query': '', 'openPage': '',
                                 'actionType': ''}
                                for item_id in stream_search_ids if item_id not in known_ids)
            citations = _citations_of(final)
            known_urls = {c['url'] for c in citations}
            citations.extend(citation for url, citation in stream_citations.items()
                             if url not in known_urls)
            return {'text': _text_of(final), 'function_calls': _calls_of(final),
                    'web_search_calls': search_calls, 'citations': citations,
                    'usage': usage}
        return {'text': ''.join(text_parts), 'function_calls': call_order,
                'web_search_calls': [{'id': item_id, 'status': '', 'query': '', 'openPage': '',
                                      'actionType': ''} for item_id in stream_search_ids],
                'citations': list(stream_citations.values()), 'usage': usage}
