"""W06 单元测试：read_web_page 抓取防护、内建搜索解析与挂载、限次与降级（agent-w06-spec.md §7.1）。

fetch 测试用本地 http.server fixture：正常正文提取 / 404 / 超时 / 私网 IP 拒绝 /
重定向到私网拒绝 / 非 HTML / 超大页面截断。SSRF 校验用 monkeypatch 的解析结果，
不发真实外网请求；fixture 环回端口经 patch.dict 放行（部署约束仅 80/443，环回拦截
仅对 fixture 主机名旁路）。行为评测场景见 agent_behavior/test_agent_behavior.py
（E35–E38，真实凭据，无 key 整组 skip）。
"""
import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from flask import Flask
from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import fetch, loop, model, registry
from petsense.db import iso_z, utc_now
from petsense.db.models import Pet
from sqlalchemy import select

ARTICLE_HTML = (
    '<html><head><title>幼猫睡眠指南</title></head><body>'
    '<article><h1>幼猫睡眠指南</h1>'
    '<p>幼猫通常每天睡十六到二十个小时，睡眠时长随年龄变化，环境安全时睡得更沉。</p>'
    '<p>第二段补充：离开母猫太早的幼猫睡眠可能更浅，需要更安静的环境。</p>'
    '</article><div id="comments"><p>网友评论：我家猫天天拆家！</p></div></body></html>').encode('utf-8')


class _FixtureHandler(BaseHTTPRequestHandler):
    """按 server.fixture 响应的极简 fixture；server.routes 按 path 覆盖；只服务本进程环回地址。"""

    def log_message(self, *args):
        pass

    def do_GET(self):
        cfg = self.server.routes.get(self.path) or self.server.fixture
        if cfg.get('sleep'):
            threading.Event().wait(cfg['sleep'])
        self.send_response(cfg.get('status', 200))
        if cfg.get('location'):
            self.send_header('Location', cfg['location'])
        if cfg.get('content_length'):
            self.send_header('Content-Length', str(cfg['content_length']))
        self.send_header('Content-Type', cfg.get('content_type', 'text/html; charset=utf-8'))
        self.end_headers()
        try:
            self.wfile.write(cfg.get('body', ARTICLE_HTML))
        except BrokenPipeError:
            pass  # 客户端按大小上限停止读取属预期行为


def start_fixture(routes=None, **cfg):
    server = ThreadingHTTPServer(('127.0.0.1', 0), _FixtureHandler)
    server.fixture = cfg
    server.routes = routes or {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}/page'


class FetchTests(unittest.TestCase):
    """fetch_page 抓取与防护。fixture 主机 127.0.0.1 的解析走旁路（返回环回地址直连本地），
    其余主机名走真实校验（配合 mock 的 getaddrinfo），确保逐跳 SSRF 复检按真实逻辑执行。"""

    def setUp(self):
        self._patches = []
        self._servers = []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for server in self._servers:
            server.shutdown()

    def _patch(self, target, attribute, *args, **kwargs):
        p = patch.object(target, attribute, *args, **kwargs)
        p.start()
        self._patches.append(p)
        return p

    def _serve(self, routes=None, **cfg):
        server = start_fixture(routes=routes, **cfg)
        self._servers.append(server[0])
        port = server[1].rsplit(':', 1)[1].split('/')[0]
        self._patch(fetch, 'ALLOWED_PORTS', {80, 443, int(port)})
        self._allow_fixture_loopback()
        return server[1]

    def _allow_fixture_loopback(self):
        """仅旁路 fixture 主机（127.0.0.1）的环回拦截，其余主机名走真实校验逻辑。"""
        original = fetch.validate_addresses

        def resolve(hostname, hop_port):
            if hostname == '127.0.0.1':
                return ['127.0.0.1']
            return original(hostname, hop_port)

        self._patch(fetch, 'validate_addresses', side_effect=resolve)

    def test_extracts_title_and_body_without_comments(self):
        page = fetch.fetch_page(self._serve())
        self.assertEqual(page['title'], '幼猫睡眠指南')
        self.assertIn('十六到二十个小时', page['text'])
        self.assertNotIn('网友评论', page['text'])
        self.assertFalse(page['truncated'])

    def test_404_reports_missing_page(self):
        url = self._serve(status=404, body=b'not found', content_type='text/plain')
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_page(url)
        self.assertIn('404', str(ctx.exception))

    def test_slow_response_reports_timeout(self):
        url = self._serve(sleep=1.5)
        self._patch(fetch, 'READ_TIMEOUT_S', 0.2)
        self._patch(fetch, 'CONNECT_TIMEOUT_S', 0.5)
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_page(url)
        self.assertIn('TimeoutError', str(ctx.exception))

    def test_rejects_non_http_and_userinfo_and_ports(self):
        for bad in ('ftp://example.com/page', 'http://user:pass@example.com/page',
                    'http://example.com:8080/page', 'javascript:alert(1)', 'http:///no-host'):
            with self.assertRaises(fetch.FetchError, msg=bad):
                fetch.fetch_page(bad)

    def test_rejects_private_and_metadata_addresses(self):
        # SSRF：解析结果落环回/私网/保留/链路本地/元数据地址一律拒绝（fail-closed）
        cases = ('127.0.0.1', '10.1.2.3', '169.254.169.254', '192.168.0.9', '172.16.0.1',
                 'fc00::1', 'fe80::1', '0.0.0.0')
        for address in cases:
            def fake_lookup(*_args, _address=address, **_kwargs):
                return [(2, 1, 6, '', (_address, 443))]
            with patch.object(socket, 'getaddrinfo', side_effect=fake_lookup):
                with self.assertRaises(fetch.FetchError) as ctx:
                    fetch.validate_addresses('example.com', 443)
            self.assertIn('不允许访问的地址', str(ctx.exception), address)

    def test_redirect_to_private_network_rejected_per_hop(self):
        # 每跳重新校验：第 1 跳到本地 fixture（302），第 2 跳目标解析为私网地址 → 拒绝
        url = self._serve(
            routes={'/next': {'status': 200, 'body': ARTICLE_HTML}},
            status=302, location='http://intranet.example.internal/next', body=b'')

        def fake_lookup(host, port=None, *_args, **_kwargs):
            if host == '127.0.0.1':  # loopback 直连按真实地址，保证 fixture 可连
                return [(2, 1, 6, '', ('127.0.0.1', port))]
            return [(2, 1, 6, '', ('10.0.0.5', 80))]  # 重定向目标解析为私网地址

        self._patch(socket, 'getaddrinfo', side_effect=fake_lookup)
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_page(url)
        self.assertIn('不允许访问的地址', str(ctx.exception))

    def test_redirect_chain_reaches_page(self):
        # 正向重定向（urljoin 与逐跳复检管道）：第 1 跳 302 → /final 正文
        url = self._serve(
            routes={'/final': {'body': ARTICLE_HTML}},
            status=302, location='/final', body=b'')
        page = fetch.fetch_page(url)
        self.assertEqual(page['url'], url.replace('/page', '/final'))
        self.assertIn('十六到二十个小时', page['text'])

    def test_rejects_non_web_content_type(self):
        url = self._serve(content_type='application/json', body=b'{"ok": true}')
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_page(url)
        self.assertIn('不是网页', str(ctx.exception))

    def test_rejects_oversized_declared_length(self):
        url = self._serve(content_length=fetch.MAX_BYTES + 1)
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_page(url)
        self.assertIn('上限', str(ctx.exception))

    def test_truncates_oversized_stream(self):
        url = self._serve()
        self._patch(fetch, 'MAX_BYTES', 300)  # fixture 页面 404 字节，300 触发流式截断
        page = fetch.fetch_page(url)
        self.assertTrue(page['truncated'])
        self.assertTrue(page['text'])

    def test_extraction_failure_is_structured(self):
        url = self._serve(body=b'<html><body><script>var x=1;</script></body></html>')
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_page(url)
        self.assertIn('未能提取到正文', str(ctx.exception))


FINAL_SEARCH = {
    'output': [
        {'type': 'web_search_call', 'id': 'ws_1', 'status': 'completed',
         'action': {'type': 'search', 'query': '上海 空气质量 今天 AQI 实时'}},
        {'type': 'web_search_call', 'id': 'ws_2', 'status': 'completed',
         'action': {'type': 'open_page', 'url': 'https://example.com/report'}},
        {'type': 'function_call', 'name': 'query_observations', 'call_id': 'c1', 'arguments': '{}'},
        {'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': '今天空气质量良好。',
             'annotations': [
                 {'type': 'url_citation', 'url_citation': {
                     'title': '上海环境监测', 'url': 'https://air.example.com/sh',
                     'content': '实时指数 42，优。' * 30}},
                 {'type': 'url_citation', 'url_citation': {
                     'title': '无地址来源', 'url': '', 'content': ''}},
             ]},
        ]},
    ],
    'usage': {'input_tokens': 100, 'output_tokens': 30},
}


class SearchParsingTests(unittest.TestCase):
    """model.respond 解析（合成 final JSON，不联网）。"""

    def test_extract_search_calls(self):
        calls = model._search_calls_of(FINAL_SEARCH)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['query'], '上海 空气质量 今天 AQI 实时')
        self.assertEqual(calls[0]['status'], 'completed')
        self.assertEqual(calls[1]['actionType'], 'open_page')
        self.assertEqual(calls[1]['openPage'], 'https://example.com/report')

    def test_extract_citations_with_snippet_cap(self):
        citations = model._citations_of(FINAL_SEARCH)
        self.assertEqual(len(citations), 1, '无 url 的 citation 应被丢弃')
        self.assertEqual(citations[0]['url'], 'https://air.example.com/sh')
        self.assertEqual(citations[0]['title'], '上海环境监测')
        self.assertLessEqual(len(citations[0]['snippet']), model.CITATION_SNIPPET_LIMIT)

    def test_mixed_output_has_function_calls_too(self):
        self.assertEqual([c['name'] for c in model._calls_of(FINAL_SEARCH)], ['query_observations'])

    def test_citations_from_item_level_annotations(self):
        # 供应商 annotations 可能在 message 项级而非 content 块级（2026-11-23 E35 归因）
        final = {'output': [{'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': '正文', 'annotations': []}],
            'annotations': [{'type': 'url_citation', 'url': 'https://item.example.com/',
                             'title': '项级来源', 'content': '片段'}]}]}
        citations = model._citations_of(final)
        self.assertEqual([c['url'] for c in citations], ['https://item.example.com/'])
        self.assertEqual(citations[0]['title'], '项级来源')


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sse_lines(events):
    """events: [(event 或 None, data_dict)] → SSE 原始字节行（不含 [DONE] 即截断流）。"""
    lines = []
    for kind, data in events:
        if kind:
            lines.append(('event: ' + kind).encode('utf-8'))
        lines.append(('data: ' + json.dumps(data, ensure_ascii=False)).encode('utf-8'))
    return lines


class RespondStreamTests(unittest.TestCase):
    """respond() 流式搜索工件回收（2026-11-23 E35 归因：completed 不保证到达）。"""

    def _respond(self, events):
        env = {'MINIMAX_API_KEY': 'test-key'}
        with patch.dict(os.environ, env), \
                patch('requests.post', return_value=_FakeStreamResponse(_sse_lines(events))):
            return model.respond([{'role': 'user', 'content': 'q'}], web_search=True)

    def test_annotation_events_survive_truncated_stream(self):
        # 流被截断（无 completed / [DONE]）：citation 从 annotation.added 事件兜底回收，
        # web_search_call 按流事件 item_id 计数（query 不可得，如实留空）
        events = [
            ('response.created', {'type': 'response.created'}),
            ('response.output_text.delta', {'type': 'response.output_text.delta', 'delta': '广州今天'}),
            ('response.output_text.delta', {'type': 'response.output_text.delta', 'delta': '有雨。'}),
            ('response.web_search_call.in_progress', {'type': 'response.web_search_call.in_progress',
                                                      'item_id': 'call_a'}),
            ('response.output_text.annotation.added', {'type': 'response.output_text.annotation.added',
                                                       'annotation': {'type': 'url_citation',
                                                                      'url': 'https://gd.example.com/',
                                                                      'title': '广州天气',
                                                                      'content': '小雨 17-23℃'}}),
            ('response.output_item.added', {'type': 'response.output_item.added',
                                            'item': {'type': 'web_search_call', 'id': 'call_b'}}),
        ]
        result = self._respond(events)
        self.assertEqual(result['text'], '广州今天有雨。')
        self.assertEqual([c['url'] for c in result['citations']], ['https://gd.example.com/'])
        self.assertEqual(sorted(c['id'] for c in result['web_search_calls']), ['call_a', 'call_b'])
        self.assertEqual(result['web_search_calls'][0]['query'], '', '流事件无 query，如实留空')

    def test_incomplete_response_is_extracted_like_final(self):
        # 长搜索流以 response.incomplete 结束（无 completed、无 [DONE]）：output 与 usage 照常提取
        final = {'type': 'response.incomplete', 'response': {
            'output': [{'type': 'web_search_call', 'id': 'ws_1', 'status': 'completed',
                        'action': {'type': 'search', 'query': '广州 天气'}}],
            'usage': {'output_tokens': 900}}}
        result = self._respond([('response.created', {'type': 'response.created'}),
                                ('response.incomplete', final)])
        self.assertEqual(result['web_search_calls'][0]['query'], '广州 天气')
        self.assertEqual(result['usage'], {'output_tokens': 900})

    def test_final_and_stream_artifacts_merged_without_duplicates(self):
        final = {'type': 'response.completed', 'response': {
            'output': [{'type': 'web_search_call', 'id': 'call_a', 'status': 'completed',
                        'action': {'type': 'search', 'query': '广州 天气'}},
                       {'type': 'message', 'role': 'assistant', 'content': [
                           {'type': 'output_text', 'text': '有雨。', 'annotations': [
                               {'type': 'url_citation', 'url': 'https://gd.example.com/',
                                'title': '广州天气', 'content': '官方'}]}]}],
            'usage': {}}}
        events = [('response.output_text.annotation.added', {
            'type': 'response.output_text.annotation.added',
            'annotation': {'type': 'url_citation', 'url': 'https://gd.example.com/',
                           'title': '流式重复', 'content': '应被去重'}}),
            ('response.output_text.annotation.added', {
                'type': 'response.output_text.annotation.added',
                'annotation': {'type': 'url_citation', 'url': 'https://other.example.com/',
                               'title': '仅流式', 'content': 'x'}}),
            ('response.completed', final)]
        result = self._respond(events)
        self.assertEqual(sorted(c['url'] for c in result['citations']),
                         ['https://gd.example.com/', 'https://other.example.com/'])
        self.assertEqual(result['citations'][0]['title'], '广州天气', 'final 提取优先于流事件')
        self.assertEqual(len(result['web_search_calls']), 1, '流 id 已在 final 中，不重复计数')


class LoopMountTests(unittest.TestCase):
    """loop 挂载策略与限次（monkeypatch 假模型，沿用 W01/W02 基建）。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalars().one().id
        self.context = {'pet_id': self.pet_id, 'user_id': self.uid, 'text': '今天空气怎么样',
                        'target': 'auto', 'history': [],
                        'snapshot': {'sections': {}, 'meta': {'chars': 10, 'trimmed': [], 'degraded': []}},
                        'now_iso': iso_z(utc_now())}
        self.stance = '{"scope":"allowed","intent":"knowledge","speaker":"assistant"}\n'

    def _turn(self, text_suffix='', calls=(), search=(), citations=()):
        return {'text': self.stance + text_suffix, 'function_calls': list(calls),
                'web_search_calls': list(search), 'citations': list(citations), 'usage': {}}

    def test_prompt_calibrates_stable_knowledge_and_round_hygiene(self):
        # E36 修复锚点：稳定常识直答、不为「显得有依据」搜索、搜索轮不混排其他工具
        prompt = loop.system_prompt(True)
        self.assertIn('猫为什么爱纸箱', prompt)
        self.assertIn('不为「显得有依据」而搜索', prompt)
        self.assertIn('不要同时调用其他工具', prompt)
        off = loop.system_prompt(False)
        self.assertNotIn('猫为什么爱纸箱', off)

    def test_search_mounted_first_round(self):
        turn = self._turn('【照料助手】今天不错。')
        with patch('petsense.agent.model.respond', return_value=turn) as respond:
            loop.run_dialogue(dict(self.context))
        self.assertTrue(respond.call_args_list[0].kwargs['web_search'],
                        '首轮 stance 未声明也必须挂载（§2.1）')
        self.assertTrue(loop.search_enabled())

    def test_search_disabled_by_env(self):
        turn = self._turn('【照料助手】今天不错。')
        with patch('petsense.agent.model.respond', return_value=turn) as respond, \
                patch.dict(os.environ, {'PETSENSE_SEARCH_DISABLED': '1'}):
            outcome = loop.run_dialogue(dict(self.context))
            schemas = registry.schemas_for(registry.DIALOGUE, 'allowed', search_enabled=False)
        self.assertFalse(respond.call_args_list[0].kwargs['web_search'])
        self.assertNotIn('read_web_page', [s['name'] for s in schemas])
        self.assertEqual([s for s in outcome['sources'] if s.get('type') == 'web'], [])
        self.assertIn('暂未开放', loop.system_prompt(False))
        self.assertNotIn('联网搜索时机：', loop.system_prompt(False))
        self.assertIn('联网搜索时机：', loop.system_prompt(True))

    def test_budget_detaches_after_two_calls(self):
        search = [{'id': 'ws_1', 'query': 'q1', 'status': 'completed', 'actionType': 'search', 'openPage': ''}]
        query_call = {'name': 'query_observations',
                      'arguments': '{"start":"2026-11-01T00:00:00+08:00","end":"2026-11-02T00:00:00+08:00"}',
                      'call_id': 'c1'}
        with patch('petsense.agent.model.respond', side_effect=[
                self._turn(calls=[query_call], search=search * 2),  # 一轮内累计 2 次
                self._turn('【照料助手】今天不错。')]) as respond, \
                patch('petsense.agent.registry.observations_query', return_value={'source': {'type': 'x'}}):
            outcome = loop.run_dialogue(dict(self.context))
        self.assertFalse(respond.call_args_list[1].kwargs['web_search'], '达到阈值后不再挂载')
        self.assertIn('search_budget_detached', outcome['trace']['degraded'])
        self.assertEqual(outcome['trace']['budget']['webSearchCalls'], 2)

    def test_read_web_page_budget_refusal(self):
        page = {'url': 'https://x.example.com/a', 'title': 't', 'text': '正文',
                'truncated': False, 'bytes': 10}
        calls = [{'name': 'read_web_page', 'arguments': '{"url":"https://x.example.com/a"}',
                  'call_id': f'c{i}'} for i in range(4)]
        turns = [self._turn(calls=[call]) for call in calls]
        turns.append(self._turn('【照料助手】读过三个页面了。'))
        with patch('petsense.agent.model.respond', side_effect=turns), \
                patch('petsense.agent.fetch.fetch_page', return_value=page):
            outcome = loop.run_dialogue(dict(self.context))
        read_entries = [e for e in outcome['trace']['tool_summary'] if e['name'] == 'read_web_page']
        self.assertEqual(len(read_entries), 4)
        self.assertEqual(sum(1 for e in read_entries if e['allowed']), 3, '前 3 次执行')
        refused = [e for e in read_entries if not e['allowed']]
        self.assertEqual([e['reason'] for e in refused], ['tool_budget_exceeded'])
        self.assertEqual(outcome['trace']['budget']['webPageReads'], 3)

    def test_citations_become_web_sources_deduped_and_capped(self):
        citations = [{'title': f'来源{i}', 'url': f'https://s.example.com/{i}', 'snippet': 'x'}
                     for i in range(8)]
        citations.append({'title': '重复', 'url': 'https://s.example.com/0', 'snippet': 'dup'})
        search = [{'id': 'w', 'query': 'q', 'status': 'completed', 'actionType': 'search', 'openPage': ''}]
        with patch('petsense.agent.model.respond',
                   return_value=self._turn('【照料助手】查到了。', search=search, citations=citations)):
            outcome = loop.run_dialogue(dict(self.context))
        web = [s for s in outcome['sources'] if s['type'] == 'web']
        self.assertEqual(len(web), loop.MAX_WEB_SOURCES, '按 url 去重并截到 6 条')
        self.assertEqual(web[0]['url'], 'https://s.example.com/0')
        self.assertEqual(web[0]['checkedAt'], self.context['now_iso'])
        entry = next(e for e in outcome['trace']['tool_summary'] if e['name'] == 'web_search')
        self.assertEqual(entry['reason'], 'server_side')
        self.assertEqual(entry['citations'], 8, '轨迹条目按去重后计数')
        self.assertIn('q', entry['arguments'])

    def test_read_web_page_success_becomes_web_source(self):
        # E35 修复：抓取成功的页面是本运行真实核对过的来源（url/title 来自页面本身）
        page = {'url': 'https://weather.example.com/gz', 'title': '广州天气网', 'text': '小雨。',
                'truncated': False, 'bytes': 10}
        call = {'name': 'read_web_page', 'arguments': '{"url":"https://weather.example.com/gz"}',
                'call_id': 'c1'}
        turns = [self._turn(calls=[call]), self._turn('【照料助手】读完了，有雨。')]
        with patch('petsense.agent.model.respond', side_effect=turns), \
                patch('petsense.agent.fetch.fetch_page', return_value=page):
            outcome = loop.run_dialogue(dict(self.context))
        web = [s for s in outcome['sources'] if s['type'] == 'web']
        self.assertEqual(len(web), 1)
        self.assertEqual(web[0]['url'], 'https://weather.example.com/gz')
        self.assertEqual(web[0]['title'], '广州天气网')
        self.assertEqual(web[0]['checkedAt'], self.context['now_iso'])

    def test_citations_collected_from_round_without_search(self):
        # E35 修复：annotations 可能出现在没有 web_search_call 的后续轮 message 上
        search = [{'id': 'ws_1', 'query': '广州 天气', 'status': 'completed',
                   'actionType': 'search', 'openPage': ''}]
        query_call = {'name': 'query_observations',
                      'arguments': '{"start":"2026-11-01T00:00:00+08:00","end":"2026-11-02T00:00:00+08:00"}',
                      'call_id': 'c1'}
        turns = [self._turn(calls=[query_call], search=search,
                            citations=[{'title': '来源A', 'url': 'https://a.example.com/', 'snippet': 'x'}]),
                 self._turn('【照料助手】好了。',
                            citations=[{'title': '来源B', 'url': 'https://b.example.com/', 'snippet': 'y'}])]
        with patch('petsense.agent.model.respond', side_effect=turns), \
                patch('petsense.agent.registry.observations_query', return_value={'source': {'type': 'x'}}):
            outcome = loop.run_dialogue(dict(self.context))
        web_urls = [s['url'] for s in outcome['sources'] if s['type'] == 'web']
        self.assertEqual(web_urls, ['https://a.example.com/', 'https://b.example.com/'],
                         '无搜索轮的 citations 也必须并入来源')

    def test_search_carryover_note_injected_with_citations(self):
        search = [{'id': 'ws_1', 'query': '广州 天气', 'status': 'completed',
                   'actionType': 'search', 'openPage': ''}]
        query_call = {'name': 'query_observations',
                      'arguments': '{"start":"2026-11-01T00:00:00+08:00","end":"2026-11-02T00:00:00+08:00"}',
                      'call_id': 'c1'}
        turns = [self._turn(calls=[query_call], search=search,
                            citations=[{'title': '广州天气网', 'url': 'https://w.example.com/',
                                        'snippet': '小雨 17-23℃'}]),
                 self._turn('【照料助手】有雨。')]
        with patch('petsense.agent.model.respond', side_effect=turns) as respond, \
                patch('petsense.agent.registry.observations_query', return_value={'source': {'type': 'x'}}):
            outcome = loop.run_dialogue(dict(self.context))
        second_input = respond.call_args_list[1].args[0]
        notes = [item['content'] for item in second_input
                 if item.get('role') == 'user' and '联网搜索记录' in (item.get('content') or '')]
        self.assertEqual(len(notes), 1, '搜索轮后下一轮输入应带回搜索记录')
        self.assertIn('https://w.example.com/', notes[0])
        self.assertIn('不要为同一问题重复搜索', notes[0])
        self.assertTrue(any('search_carryover' in m for m in outcome['trace']['messages']))

    def test_search_carryover_note_honest_when_results_missing(self):
        # 「搜索+工具调用」同轮且结果未返回（E35 归因形状）：如实说明，不声称已引用
        search = [{'id': 'call_a', 'query': '', 'status': '', 'actionType': '', 'openPage': ''}]
        query_call = {'name': 'query_observations',
                      'arguments': '{"start":"2026-11-01T00:00:00+08:00","end":"2026-11-02T00:00:00+08:00"}',
                      'call_id': 'c1'}
        turns = [self._turn(calls=[query_call], search=search),
                 self._turn('【照料助手】我不能确认实时天气。')]
        with patch('petsense.agent.model.respond', side_effect=turns), \
                patch('petsense.agent.registry.observations_query', return_value={'source': {'type': 'x'}}):
            outcome = loop.run_dialogue(dict(self.context))
        web = [s for s in outcome['sources'] if s['type'] == 'web']
        self.assertEqual(web, [], '无 citations、无成功抓取时不得编造网页来源')
        entry = next(e for e in outcome['trace']['tool_summary'] if e['name'] == 'web_search')
        self.assertEqual(entry['citations'], 0)
        self.assertEqual(json.loads(entry['arguments'])['queries'], [], 'query 不可得时如实留空')

    def test_out_of_scope_with_search_is_audited(self):
        turn = {'text': '{"scope":"out_of_scope","intent":"write","speaker":"assistant"}\n【照料助手】这超出范围。',
                'function_calls': [], 'usage': {},
                'web_search_calls': [{'id': 'w', 'query': 'q', 'status': 'completed',
                                      'actionType': 'search', 'openPage': ''}],
                'citations': []}
        with patch('petsense.agent.model.respond', return_value=turn):
            outcome = loop.run_dialogue(dict(self.context))
        self.assertIn('search_on_out_of_scope', outcome['trace']['degraded'])
        self.assertEqual(outcome['trace']['conclusion']['scope'], 'out_of_scope')

    def test_search_progress_hint_passes_through_filter(self):
        seen = []
        stream = loop.DeltaFilter(seen.append)
        stream.feed(model.SEARCH_PROGRESS_HINT)
        self.assertEqual(seen, [model.SEARCH_PROGRESS_HINT])
        header = '{"scope":"allowed","intent":"read","speaker":"assistant"}'
        stream.feed(header[:18])
        self.assertEqual(seen, [model.SEARCH_PROGRESS_HINT])
        stream.feed(header + '\n上海今天空气优良。')
        self.assertEqual(seen, [model.SEARCH_PROGRESS_HINT, '上海今天空气优良。'])

    def test_stance_only_tool_round_has_no_visible_text(self):
        seen = []
        stream = loop.DeltaFilter(seen.append)
        stream.feed('{"scope":"allowed","intent":"read","speaker":"assistant"}')
        stream.close()
        self.assertEqual(seen, [])


class RegistryGateTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalars().one().id
        self.context = {'pet_id': self.pet_id, 'user_id': self.uid}

    def test_read_web_page_gated_with_timeout(self):
        gate = registry.execute_tool('read_web_page', {'url': 'https://x.example.com/'},
                                     'dialogue', None, self.context)
        self.assertEqual(gate['reason'], 'stance_missing')
        gate = registry.execute_tool('read_web_page', {'url': 'https://x.example.com/'},
                                     'dialogue', 'out_of_scope', self.context)
        self.assertEqual(gate['reason'], 'scope_not_allowed')
        gate = registry.execute_tool('read_web_page', {'url': 'https://x.example.com/'},
                                     'review', 'allowed', self.context)
        self.assertEqual(gate['reason'], 'form_not_allowed')
        self.assertEqual(registry._TOOLS['read_web_page'].timeout_s, 20.0)
        self.assertEqual(registry._TOOLS['query_observations'].timeout_s, registry.TOOL_TIMEOUT_S)

    def test_schemas_follow_search_switch(self):
        names_on = [s['name'] for s in registry.schemas_for(registry.DIALOGUE, 'allowed')]
        self.assertIn('read_web_page', names_on)
        names_off = [s['name'] for s in registry.schemas_for(registry.DIALOGUE, 'allowed',
                                                             search_enabled=False)]
        self.assertNotIn('read_web_page', names_off)

    def test_read_web_page_fetch_error_is_structured(self):
        with patch('petsense.agent.fetch.fetch_page',
                   side_effect=fetch.FetchError('网页读取失败：HTTP 404')):
            gate = registry.execute_tool('read_web_page', {'url': 'https://x.example.com/'},
                                         'dialogue', 'allowed', self.context)
        self.assertTrue(gate['allowed'])
        self.assertIn('不得当作来源不存在', gate['result']['error'])


if __name__ == '__main__':
    unittest.main()
