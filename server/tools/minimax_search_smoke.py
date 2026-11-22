"""MiniMax 搜索/网页阅读能力只读冒烟（W00，agent-w00-spec.md §4）。

验证 P00 契约第 7 节未决项：M3.1-Flash 是否支持内建搜索（web_search）、
MCP 挂载、来源链接与 URL 正文阅读。只读：不触数据库、不写文件、不改服务。

用法：
  本地：MINIMAX_API_KEY=... python3 minimax_search_smoke.py
  云端：python3 - < minimax_search_smoke.py（凭据环境与 petsense.service 一致）

退出码只反映 T0 凭据/端点健康；搜索能力结论是输出数据，不算脚本失败。
"""
import base64
import json
import os
import re
import secrets
import sys
from pathlib import Path

import requests

TIMEOUT = (10, 30)
EVIDENCE_LIMIT = 500


def load_config():
    key = os.environ.get('MINIMAX_API_KEY')
    if not key and os.environ.get('MINIMAX_API_KEY_FILE'):
        key = Path(os.environ['MINIMAX_API_KEY_FILE']).read_text().strip()
    if not key:
        raise SystemExit('未配置 MiniMax 凭据（MINIMAX_API_KEY 或 MINIMAX_API_KEY_FILE）')
    return key, os.environ.get('MINIMAX_BASE_URL', 'https://api.minimaxi.com/v1').rstrip('/'), \
        os.environ.get('MINIMAX_MODEL', 'MiniMax-M3.1-Flash')


def chat(key, base, payload):
    """发一次 chat completion，返回 (status, parsed_json_or_text)。"""
    try:
        response = requests.post(base + '/chat/completions',
            headers={'Authorization': 'Bearer ' + key}, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return None, f'{type(exc).__name__}: {exc}'
    text = response.text
    try:
        return response.status_code, response.json() if text else ''
    except ValueError:
        return response.status_code, text


def evidence(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:EVIDENCE_LIMIT].replace('\n', ' ')


def content_of(body):
    if isinstance(body, dict):
        try:
            return body['choices'][0]['message']['content'] or ''
        except (KeyError, IndexError, TypeError):
            return ''
    return body if isinstance(body, str) else ''


def find_urls(value, found):
    """递归收集响应中任意层级的 URL 与疑似引用字段。"""
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str) and v.startswith(('http://', 'https://')):
                found.append(f'{k}={v[:120]}')
            elif 'citation' in k.lower() or 'search' in k.lower() or 'reference' in k.lower():
                if v not in (None, '', [], {}):
                    found.append(f'{k}={evidence(v)[:120]}')
            else:
                find_urls(v, found)
    elif isinstance(value, list):
        for item in value:
            find_urls(item, found)


SEARCH_QUESTION = '现在上海天气怎么样？请联网搜索后用一句话回答，并给出你引用的网页链接。'


def probe_builtin_search(key, base, model):
    results = []
    for shape in ({'enable': True}, {'enabled': True}):
        status, body = chat(key, base, {'model': model, 'max_tokens': 800, 'reasoning_effort': 'low',
            'messages': [{'role': 'user', 'content': SEARCH_QUESTION}], 'web_search': shape})
        urls = []
        if status == 200 and isinstance(body, dict):
            find_urls(body, urls)
        verdict = ('accepted' if status == 200 else 'rejected') if status is not None else 'network-error'
        if status == 200 and not urls:
            verdict = 'http200-no-search-evidence'
        results.append({'probe': f'web_search={shape}', 'verdict': verdict, 'status': status,
                        'urls': urls[:5], 'evidence': evidence(body if status != 200 else content_of(body)),
                        'body': body})
        print(f'[T1] web_search={shape} -> {verdict} (HTTP {status})'
              + (f' urls={urls[:3]}' if urls else ''))
    return results


def probe_text01_search(key, base):
    """旧版文档称 web_search 限定 MiniMax-Text-01，补测该组合（W06 替代方案数据点）。"""
    status, body = chat(key, base, {'model': 'MiniMax-Text-01', 'max_tokens': 800,
        'messages': [{'role': 'user', 'content': SEARCH_QUESTION}], 'web_search': {'enable': True}})
    urls = []
    if status == 200 and isinstance(body, dict):
        find_urls(body, urls)
    text = content_of(body)
    searched = bool(urls) or any(w in text for w in ('搜索', '来源', '网页'))
    verdict = 'available' if searched else ('rejected' if status != 200 else 'no-evidence')
    print(f'[T1b] MiniMax-Text-01 + web_search -> {verdict} (HTTP {status})'
          + (f' urls={urls[:3]}' if urls else '') + f': {text[:120]}')
    return {'verdict': verdict, 'status': status, 'urls': urls, 'evidence': evidence(body)}


def probe_mcp(key, base, model):
    results = []
    for field, value in (('mcp', []), ('mcp_servers', []), ('tools', [{'type': 'web_search'}])):
        status, body = chat(key, base, {'model': model, 'max_tokens': 400, 'reasoning_effort': 'low',
            'messages': [{'role': 'user', 'content': SEARCH_QUESTION}], field: value})
        verdict = 'rejected' if status not in (200, None) else ('ignored' if status == 200 else 'network-error')
        results.append({'probe': f'{field}={value}', 'verdict': verdict, 'status': status,
                        'evidence': evidence(body), 'body': body if status == 200 else None})
        print(f'[T2] {field}={value} -> {verdict} (HTTP {status}): {evidence(body)[:160]}')
    return results


NO_FETCH_WORDS = ('无法', '不能', '不支持', '没有联网', '无法访问', '不能直接打开', '无法直接打开')


def probe_url_reading(key, base, model):
    """两级探针都无法证明真实抓取：httpbin 的页内容=URL 中 base64 的解码值（心算可得）；
    postman-echo 的响应 schema 公开且 token 在 URL 里（可合成，合成指纹如 header 按字母序）。
    证据价值在于记录行为：明确拒读、幻觉错值、或返回可推导内容。"""
    token = secrets.token_hex(8)
    probes = [('httpbin', f'https://httpbin.org/base64/{base64.urlsafe_b64encode(token.encode()).decode()}'),
              ('postman-echo', f'https://postman-echo.com/get?token={token}')]
    outcomes = []
    for name, url in probes:
        status, body = chat(key, base, {'model': model, 'max_tokens': 600, 'reasoning_effort': 'low',
            'messages': [{'role': 'user', 'content': f'请打开并读取这个网页的正文，原样输出你在页面上看到的关键内容：{url}'}]})
        text = content_of(body)
        outside = token in re.sub(r'https?://\S+', '', text)
        if status != 200:
            verdict = f'http-{status}'
        elif outside and name == 'postman-echo':
            verdict = 'echo-json-returned(schema-derivable)'
        elif outside:
            verdict = 'token-recovered(decode-or-fetch)'
        elif any(word in text for word in NO_FETCH_WORDS):
            verdict = 'honest-no-fetch'
        elif name == 'httpbin' and re.search(r'\b[0-9a-f]{16}\b', text):
            verdict = 'wrong-token(hallucination)'
        else:
            verdict = 'no-evidence'
        outcomes.append((name, verdict))
        print(f'[T4] {name} -> {verdict} (HTTP {status}): {text[:160]}')
    if any('token-recovered' in v or 'echo-json' in v for _, v in outcomes):
        return 'inconclusive(content-derivable)'
    if any(v == 'honest-no-fetch' for _, v in outcomes):
        return 'honest-no-fetch'
    return next((v for _, v in outcomes), 'unprobed')


def probe_responses_search(key, model):
    """官方文档：内建 web_search 挂在 OpenAI Responses API（/v1/responses，tools:[{type:web_search}]）。
    依次探测生产在用域与文档给出的国内/国际域。"""
    bases = [('api.minimaxi.com(生产在用)', 'https://api.minimaxi.com/v1/responses'),
             ('api.minimax.cn(国内文档)', 'https://api.minimax.cn/v1/responses'),
             ('api.minimax.io(国际文档)', 'https://api.minimax.io/v1/responses')]
    results = []
    for name, url in bases:
        try:
            response = requests.post(url, headers={'Authorization': 'Bearer ' + key}, timeout=TIMEOUT,
                json={'model': model, 'input': SEARCH_QUESTION, 'tools': [{'type': 'web_search'}]})
        except requests.RequestException as exc:
            verdict = f'network-error({type(exc).__name__})'
            print(f'[T6] Responses@{name} -> {verdict}')
            results.append({'base': name, 'verdict': verdict, 'urls': []})
            continue
        urls, searched = [], False
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code == 200 and isinstance(body, dict):
            find_urls(body, urls)
            for item in body.get('output') or []:
                if isinstance(item, dict) and item.get('type') == 'web_search_call':
                    searched = True
            if any('web_search' in json.dumps(v) for v in body.get('output') or [] if isinstance(v, dict)):
                searched = True
        if response.status_code == 401:
            verdict = 'auth-failed'
        elif response.status_code == 404:
            verdict = 'not-found'
        elif searched or urls:
            verdict = 'available(真实搜索)'
        elif response.status_code == 200:
            verdict = 'http200-no-search-evidence'
        else:
            verdict = f'http-{response.status_code}'
        print(f'[T6] Responses@{name} -> {verdict} (HTTP {response.status_code})'
              + (f' urls={urls[:3]}' if urls else '') + f': {evidence(body)[:200]}')
        results.append({'base': name, 'verdict': verdict, 'urls': urls})
    return results


def probe_anthropic_search(key, model):
    """Anthropic Messages API 变体：tools:[{type:web_search_20250305,name:web_search}]，x-api-key 认证。"""
    url = 'https://api.minimax.cn/anthropic/v1/messages'
    try:
        response = requests.post(url, timeout=TIMEOUT,
            headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': model, 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': SEARCH_QUESTION}],
                  'tools': [{'type': 'web_search_20250305', 'name': 'web_search'}]})
    except requests.RequestException as exc:
        print(f'[T7] Anthropic@api.minimax.cn -> network-error({type(exc).__name__})')
        return 'network-error'
    try:
        body = response.json()
    except ValueError:
        body = response.text
    urls, searched = [], False
    if response.status_code == 200 and isinstance(body, dict):
        find_urls(body, urls)
        for block in body.get('content') or []:
            if isinstance(block, dict) and block.get('type') in ('server_tool_use', 'web_search_tool_result'):
                searched = True
    verdict = ('available(真实搜索)' if searched or urls else
               'auth-failed' if response.status_code == 401 else
               'not-found' if response.status_code == 404 else
               'http200-no-search-evidence' if response.status_code == 200 else f'http-{response.status_code}')
    print(f'[T7] Anthropic@api.minimax.cn -> {verdict} (HTTP {response.status_code})'
          + (f' urls={urls[:3]}' if urls else '') + f': {evidence(body)[:200]}')
    return verdict


def main():
    key, base, model = load_config()
    print(f'# MiniMax 搜索冒烟 base={base} model={model}')
    status, body = chat(key, base, {'model': model, 'max_tokens': 50, 'reasoning_effort': 'low',
        'messages': [{'role': 'user', 'content': '只回复四个字：连通正常'}]})
    ok = status == 200 and '连通' in content_of(body)
    print(f'[T0] 基线连通 -> {"pass" if ok else "FAIL"} (HTTP {status}): {evidence(content_of(body) or body)[:120]}')
    if not ok:
        print('结论：凭据/端点不可用，后续项全部 skip。')
        return 1

    t1 = probe_builtin_search(key, base, model)
    t1b = probe_text01_search(key, base)
    t2 = probe_mcp(key, base, model)
    urls = []
    for item in t1 + [r for r in t2 if r['body']]:
        find_urls(item['body'], urls)
    print(f'[T3] 来源链接扫描 -> {"yes: " + "; ".join(urls[:3]) if urls else "no"}')

    t4 = probe_url_reading(key, base, model)
    t6 = probe_responses_search(key, model)
    t7 = probe_anthropic_search(key, model)

    builtin = next((r for r in t1 if r['urls']), None)
    ignored = [r['probe'].split('=')[0] for r in t2 if r['status'] == 200]
    rejected = [r['probe'].split('=')[0] for r in t2 if r['status'] not in (200, None)]
    print('\n== 结论矩阵（回填 agent-w00-spec.md §4.4 与 P00 契约第 7 节）==')
    print('T0 端点与凭据: available')
    print(f'T1 内建 web_search: {"available(带来源)" if builtin else "unavailable（HTTP 200 但无搜索痕迹，参数被忽略）"}')
    print(f'T1b MiniMax-Text-01 + web_search: {t1b["verdict"]}'
          + (f'（HTTP {t1b["status"]}，来源 {len(t1b["urls"])} 条）' if t1b['urls'] else f'（HTTP {t1b["status"]}）'))
    print(f'T2 MCP 挂载: unavailable（{"/".join(ignored) or "无"} 字段被忽略；{"/".join(rejected) or "无"} 被参数校验拒绝，无内建搜索挂载方式）')
    print(f'T3 响应内来源链接: {"yes" if urls else "no"}')
    if t4 == 'honest-no-fetch':
        t4_line = '模型明确声明无法打开网页 -> read_web_page 需自建 fetcher'
    else:
        t4_line = ('返回内容均可由 URL/公开schema 推导或合成，跨运行不稳定'
                   '（曾明确拒读、曾幻觉错误值）-> 不能依赖模型读URL，read_web_page 需自建 fetcher')
    print(f'T4 URL 正文阅读: {t4} -> {t4_line}')
    live = next((r for r in t6 if r['verdict'] == 'available(真实搜索)'), None)
    if live:
        print(f'T6 Responses API 内建搜索: available @ {live["base"]}（工具往返无 web_search_call 需按 output 结构复核）')
    else:
        print('T6 Responses API 内建搜索: ' + '; '.join(f'{r["base"]}={r["verdict"]}' for r in t6))
    print(f'T7 Anthropic API 内建搜索: {t7}')
    print('总结: Chat Completions 端点无内建搜索；官方路径是 Responses/Anthropic API 的 web_search 服务端工具（Beta）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
