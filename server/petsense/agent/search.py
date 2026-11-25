"""联网搜索执行层（W19，agent-w19-spec.md §3）。

调用**供应商的独立搜索接口**并归一化结果。动机：Moonshot Kimi 的 Responses 内建
`web_search` 工具被接受但不执行（无 web_search_call、无 url_citation，模型会自述
「无法联网」），MiniMax 的内建搜索又不通用；供应商独立搜索接口（Kimi 国内节点
`/tools/search_pro`，¥0.015/次）实测可用，因此联网搜索改为**本地工具**执行：
模型调用 `search_web` → 服务端调供应商搜索接口 → 结果回灌模型并进来源卡。

供应商差异全部由环境变量表达（凭据与 base 复用模型服务的同一份）：
- `MINIMAX_SEARCH_PATH`：搜索接口路径，默认 `/tools/search_pro`
- `MINIMAX_SEARCH_QUERY_FIELD`：查询词字段名，默认 `text_query`
- `MINIMAX_SEARCH_LIMIT`：默认返回条数，默认 5（上限 8）

失败一律返回结构化 error（与 read_web_page 同一口径），不抛裸异常给模型；
模型据此换查询词或如实说明，不得当作「查不到」。
"""
import os

import requests

from petsense.agent import model as agent_model

SEARCH_TIMEOUT_S = (10, 30)
SEARCH_PATH_ENV = 'MINIMAX_SEARCH_PATH'
SEARCH_QUERY_FIELD_ENV = 'MINIMAX_SEARCH_QUERY_FIELD'
SEARCH_LIMIT_ENV = 'MINIMAX_SEARCH_LIMIT'
DEFAULT_SEARCH_PATH = '/tools/search_pro'
DEFAULT_QUERY_FIELD = 'text_query'
DEFAULT_LIMIT = 5
MAX_LIMIT = 8
CHUNK_CHAR_LIMIT = 1200


def search_path():
    return (os.environ.get(SEARCH_PATH_ENV) or DEFAULT_SEARCH_PATH).strip() or DEFAULT_SEARCH_PATH


def query_field():
    return (os.environ.get(SEARCH_QUERY_FIELD_ENV) or DEFAULT_QUERY_FIELD).strip() or DEFAULT_QUERY_FIELD


def default_limit():
    try:
        value = int(os.environ.get(SEARCH_LIMIT_ENV) or DEFAULT_LIMIT)
    except ValueError:
        value = DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _normalize(entry):
    """供应商结果条目 → 统一字段；正文片段合并成 content（截断）。"""
    if not isinstance(entry, dict):
        return None
    url = str(entry.get('url') or '').strip()
    if not url:
        return None
    parts = []
    for chunk in entry.get('chunks') or []:
        if isinstance(chunk, dict):
            text = chunk.get('text')
        else:
            text = chunk
        if text:
            parts.append(str(text))
    content = '\n'.join(parts)[:CHUNK_CHAR_LIMIT]
    snippet = str(entry.get('snippet') or '').strip()
    return {'title': str(entry.get('title') or '').strip(), 'url': url,
            'site': str(entry.get('site_name') or '').strip(),
            'date': str(entry.get('date') or '').strip(),
            'snippet': snippet or content[:200], 'content': content}


def run_search(query, limit=None):
    """执行一次搜索。返回 {'query','results','count'} 或 {'error': 原因}。"""
    query = str(query or '').strip()
    if not query:
        return {'error': '请提供搜索关键词。'}
    key, base, _model = agent_model.load_credentials()
    try:
        response = requests.post(base.rstrip('/') + search_path(),
                                 headers={'Authorization': 'Bearer ' + key},
                                 json={query_field(): query, 'limit': limit or default_limit(),
                                       'timeout_seconds': 30},
                                 timeout=SEARCH_TIMEOUT_S)
    except requests.RequestException as exc:
        return {'error': f'联网搜索请求失败：{type(exc).__name__}'}
    if response.status_code != 200:
        return {'error': f'联网搜索接口返回 HTTP {response.status_code}'}
    try:
        body = response.json()
    except ValueError:
        return {'error': '联网搜索接口返回了无法解析的内容。'}
    raw_results = body.get('search_results') if isinstance(body, dict) else None
    if not isinstance(raw_results, list):
        return {'error': '联网搜索接口没有返回结果列表。'}
    results = []
    for entry in raw_results:
        normalized = _normalize(entry)
        if normalized:
            results.append(normalized)
    if not results:
        return {'query': query, 'results': [], 'count': 0,
                'note': '没有检索到结果；换关键词或如实说明查不到，不要编造来源。'}
    return {'query': query, 'results': results, 'count': len(results)}
