"""MiniMax W06 混排搜索冒烟（只读，agent-w06-spec.md §3）。

复现 W06 定稿前的实测形态：/v1/responses 流式 + function 与内建 web_search 混排，
采样延迟与 citation 数量，验证挂载/解析约束（读超时 110s、多 web_search_call、
url_citation annotations）。只读：不触数据库、不写文件、不回显凭据。

用法：
  本地：MINIMAX_API_KEY=... python3 minimax_w06_smoke.py [次数]
  云端：python3 - < minimax_w06_smoke.py（凭据环境与 petsense.service 一致）

退出码只反映 T0 凭据/端点健康；延迟方差与计费字段结论是输出数据。
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

TIMEOUT = (10, 110)  # 挂搜索的请求读超时 110s（W06 spec D4）


def load_config():
    key = os.environ.get('MINIMAX_API_KEY')
    if not key and os.environ.get('MINIMAX_API_KEY_FILE'):
        key = Path(os.environ['MINIMAX_API_KEY_FILE']).read_text().strip()
    if not key:
        raise SystemExit('未配置 MiniMax 凭据（MINIMAX_API_KEY 或 MINIMAX_API_KEY_FILE）')
    return key, os.environ.get('MINIMAX_BASE_URL', 'https://api.minimaxi.com/v1').rstrip('/'), \
        os.environ.get('MINIMAX_MODEL', 'MiniMax-M3.1-Flash')


def probe(key, base, model, question):
    """一次混排搜索往返（流式），返回统计与 citation 列表。"""
    payload = {'model': model, 'stream': True, 'reasoning_effort': 'low',
               'max_output_tokens': 1800,
               'input': [{'role': 'system', 'content': '你是宠物健康助手，回答须给出来源。'},
                         {'role': 'user', 'content': question}],
               'tools': [{'type': 'function', 'name': 'noop_probe',
                          'description': '仅用于验证混排的工具桩',
                          'parameters': {'type': 'object', 'properties': {}}},
                         {'type': 'web_search'}]}
    started = time.monotonic()
    first_byte = None
    search_events = 0
    text_parts = []
    final = None
    with requests.post(base + '/responses', headers={'Authorization': 'Bearer ' + key},
                       json=payload, timeout=TIMEOUT, stream=True) as response:
        if response.status_code != 200:
            return {'status': response.status_code, 'error': response.text[:300]}
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
            kind = current_event or item.get('type') or ''
            if first_byte is None:
                first_byte = time.monotonic() - started
            if 'web_search_call' in kind:
                search_events += 1
            if kind.endswith('output_text.delta') and isinstance(item.get('delta'), str):
                text_parts.append(item['delta'])
            if kind == 'response.completed' and isinstance(item.get('response'), dict):
                final = item['response']
    elapsed = time.monotonic() - started
    calls, citations = [], []
    if final:
        for out in final.get('output') or []:
            if not isinstance(out, dict):
                continue
            if out.get('type') == 'web_search_call':
                action = out.get('action') or {}
                calls.append(action.get('query') or action.get('url') or action.get('type'))
            if out.get('type') in ('message', None):
                for block in out.get('content') or []:
                    if not isinstance(block, dict):
                        continue
                    for note in block.get('annotations') or []:
                        cite = (note.get('url_citation') or {}) if isinstance(note, dict) else {}
                        if cite.get('url'):
                            citations.append({'title': cite.get('title') or '',
                                              'url': cite['url']})
    usage = (final or {}).get('usage') or {}
    return {'status': 200, 'elapsed': round(elapsed, 1), 'first_byte': round(first_byte or -1, 1),
            'search_events': search_events, 'calls': calls, 'citations': citations,
            'billing_fields': sorted(usage.keys()),
            'text_head': ''.join(text_parts)[:160]}


def main():
    key, base, model = load_config()
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    question = os.environ.get('W06_SMOKE_QUESTION', '今天上海的空气质量怎么样？适合带狗出门遛弯吗？')
    print(f'# W06 混排搜索冒烟 base={base} model={model} samples={samples}')
    # T0 基线：不挂 tools 的最小往返
    try:
        r = requests.post(base + '/responses', headers={'Authorization': 'Bearer ' + key},
                          json={'model': model, 'input': '只回复四个字：连通正常'},
                          timeout=(10, 30))
        ok = r.status_code == 200 and '连通' in r.text
        print(f'[T0] 基线连通 -> {"pass" if ok else "FAIL"} (HTTP {r.status_code})')
        if not ok:
            return 1
    except requests.RequestException as exc:
        print(f'[T0] 基线连通 -> network-error({type(exc).__name__})')
        return 1
    for i in range(samples):
        result = probe(key, base, model, question)
        if result.get('status') != 200:
            print(f'[S{i + 1}] HTTP {result["status"]}: {result.get("error", "")[:200]}')
            continue
        print(f'[S{i + 1}] {result["elapsed"]}s（首字节 {result["first_byte"]}s）'
              f' 搜索事件 {result["search_events"]} 次、调用 {result["calls"]} 条、'
              f'citation {len(result["citations"])} 条；usage 字段 {result["billing_fields"]}')
        if result['citations']:
            first = result['citations'][0]
            print(f'      首条来源：{first["title"] or "(无标题)"} {first["url"]}')
        print(f'      正文开头：{result["text_head"]}')
    print('\n结论要点：延迟方差（D4 的 110s 读超时依据）、单请求多条 web_search_call（§3.4）、'
          'usage 是否含搜索计费字段（D6 控制台核对）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
