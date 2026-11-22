"""MiniMax Responses API 适配层（W01，agent-w01-spec.md §3.1；W06 内建 web_search）。

只负责传输与解析：凭据加载、/v1/responses 请求、SSE 事件流解析（输出文本增量、
函数调用、内建 web_search_call、url_citation、usage）。循环编排在 loop.py；
本文件不做任何业务判断。

实测约束（2026-11-22 冒烟，tools/minimax_responses_smoke.py / minimax_w06_smoke.py；
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
"""
import json
import os
from pathlib import Path

import requests

REQUEST_TIMEOUT = (10, 60)
SEARCH_READ_TIMEOUT_S = 110.0
SEARCH_PROGRESS_HINT = '正在联网核实…'
CITATION_SNIPPET_LIMIT = 200


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
