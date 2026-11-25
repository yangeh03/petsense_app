"""W19 单元测试：Chat Completions 传输形态与本地联网搜索（agent-w19-spec.md §5）。

覆盖：
- 请求形态选择（显式环境变量 > base 推断 > 默认 responses）；
- Responses 形态会话/工具 → Chat 形态的转换（function_call → assistant.tool_calls、
  function_call_output → role=tool、图片块 → image_url）；
- Chat 流式解析：正文增量与 tool_calls 分片合并、usage 收取、on_delta 回调；
- thinking 关闭开关（仅 K2 系列支持该参数）；
- 本地 search_web 工具：结果归一化、结构化失败、空结果提示、按供应商能力灰度挂载；
- loop 集成：搜索结果进网页来源卡、单次运行搜索限次。

不联网：模型传输与搜索接口全部打桩。
"""
import json
import os
import unittest
from unittest.mock import patch

from helpers import fresh_db
from test_pets import seed_user_with_pet

from petsense import db
from petsense.agent import loop, model, registry, search
from petsense.db import iso_z, utc_now
from petsense.db.models import Pet
from sqlalchemy import select


class _FakeStreamResponse:
    def __init__(self, lines, status_code=200, text=''):
        self._lines = lines
        self.status_code = status_code
        self.text = text

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _chat_sse(chunks):
    """chunks: [dict] → Chat Completions 的 SSE 原始字节行。"""
    lines = []
    for chunk in chunks:
        lines.append(('data: ' + json.dumps(chunk, ensure_ascii=False)).encode('utf-8'))
    lines.append(b'data: [DONE]')
    return lines


def _delta(content=None, tool_calls=None):
    delta = {}
    if content is not None:
        delta['content'] = content
    if tool_calls is not None:
        delta['tool_calls'] = tool_calls
    return {'choices': [{'index': 0, 'delta': delta}]}


class ModeSelectionTests(unittest.TestCase):
    def test_explicit_env_wins(self):
        with patch.dict(os.environ, {'MINIMAX_API_MODE': 'responses'}):
            self.assertEqual(model.api_mode('https://api.moonshot.cn/v1'), 'responses')

    def test_base_inference_for_moonshot(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MINIMAX_API_MODE', None)
            self.assertEqual(model.api_mode('https://api.moonshot.cn/v1'), 'chat')
            self.assertEqual(model.api_mode('https://api.minimaxi.com/v1'), 'responses')

    def test_thinking_flag(self):
        with patch.dict(os.environ, {'MINIMAX_THINKING_DISABLED': '1'}):
            self.assertTrue(model.thinking_disabled())
        with patch.dict(os.environ, {'MINIMAX_THINKING_DISABLED': ''}):
            self.assertFalse(model.thinking_disabled())


class ChatConversionTests(unittest.TestCase):
    def test_messages_conversion(self):
        items = [
            {'role': 'system', 'content': '你是助手'},
            {'role': 'user', 'content': '它昨天走动多吗？'},
            {'type': 'function_call', 'call_id': 'call_1', 'name': 'query_observations',
             'arguments': '{"start":"2026-11-24T00:00:00+08:00"}'},
            {'type': 'function_call_output', 'call_id': 'call_1', 'output': '{"observedSeconds":10}'},
        ]
        messages = model._chat_messages(items)
        self.assertEqual([m['role'] for m in messages],
                         ['system', 'user', 'assistant', 'tool'])
        self.assertEqual(messages[2]['tool_calls'][0]['id'], 'call_1')
        self.assertEqual(messages[2]['tool_calls'][0]['function']['name'], 'query_observations')
        self.assertEqual(messages[3]['tool_call_id'], 'call_1')
        self.assertEqual(messages[3]['content'], '{"observedSeconds":10}')

    def test_image_block_conversion(self):
        items = [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': '这是什么'},
            {'type': 'input_image', 'image_url': 'data:image/png;base64,AAA'}]}]
        parts = model._chat_messages(items)[0]['content']
        self.assertEqual(parts[0], {'type': 'text', 'text': '这是什么'})
        self.assertEqual(parts[1]['image_url']['url'], 'data:image/png;base64,AAA')

    def test_tool_schema_conversion(self):
        tools = [{'type': 'function', 'name': 'search_web', 'description': '搜索',
                  'parameters': {'type': 'object', 'properties': {}}}]
        converted = model._chat_tools(tools)
        self.assertEqual(converted[0]['type'], 'function')
        self.assertEqual(converted[0]['function']['name'], 'search_web')
        self.assertIn('parameters', converted[0]['function'])


class ChatStreamTests(unittest.TestCase):
    def _respond(self, chunks, **env):
        environment = {'MINIMAX_API_KEY': 'test-key', 'MINIMAX_API_MODE': 'chat',
                       'MINIMAX_BASE_URL': 'https://api.moonshot.cn/v1'}
        environment.update(env)
        seen = {}
        with patch.dict(os.environ, environment), \
                patch('requests.post', return_value=_FakeStreamResponse(_chat_sse(chunks))) as post:
            result = model.respond([{'role': 'user', 'content': 'q'}],
                                    tools=[{'type': 'function', 'name': 'search_web',
                                            'description': '搜索', 'parameters': {}}],
                                    on_delta=lambda text: seen.setdefault('deltas', []).append(text))
        return result, post, seen

    def test_text_and_tool_calls_merged(self):
        chunks = [
            _delta(content='上海'),
            _delta(content='今天有雨。'),
            _delta(tool_calls=[{'index': 0, 'id': 'call_x', 'type': 'function',
                                 'function': {'name': 'search_web', 'arguments': '{"query":"'}}]),
            _delta(tool_calls=[{'index': 0, 'function': {'arguments': '天气"}'}}]),
            {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20}},
        ]
        result, post, seen = self._respond(chunks)
        self.assertEqual(result['text'], '上海今天有雨。')
        self.assertEqual(len(result['function_calls']), 1)
        self.assertEqual(result['function_calls'][0]['name'], 'search_web')
        self.assertEqual(result['function_calls'][0]['arguments'], '{"query":"天气"}')
        self.assertEqual(result['function_calls'][0]['call_id'], 'call_x')
        self.assertEqual(result['usage'].get('completion_tokens'), 20)
        self.assertEqual(seen['deltas'][-1], '上海今天有雨。', '增量回调应给出累计正文')

    def test_chat_mode_ignores_provider_web_search(self):
        result, post, _ = self._respond([_delta(content='好')])
        payload = post.call_args.kwargs['json']
        self.assertNotIn('web_search', payload.get('tools', []))
        self.assertEqual(result['web_search_calls'], [])
        self.assertEqual(result['citations'], [])

    def test_thinking_disabled_flag_sent(self):
        _, post, _ = self._respond([_delta(content='好')],
                                   MINIMAX_THINKING_DISABLED='1')
        self.assertEqual(post.call_args.kwargs['json'].get('thinking'), {'type': 'disabled'})

    def test_chat_endpoint_used(self):
        _, post, _ = self._respond([_delta(content='好')])
        self.assertTrue(post.call_args.args[0].endswith('/chat/completions'))


class _FakeSearchResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class SearchToolTests(unittest.TestCase):
    def _run(self, payload, status_code=200, **env):
        environment = {'MINIMAX_API_KEY': 'test-key',
                       'MINIMAX_BASE_URL': 'https://api.moonshot.cn/v1'}
        environment.update(env)
        with patch.dict(os.environ, environment), \
                patch('requests.post', return_value=_FakeSearchResponse(payload, status_code)):
            return search.run_search('上海 天气')

    def test_results_normalized(self):
        outcome = self._run({'search_results': [
            {'title': '上海天气预报', 'url': 'https://weather.example.com/sh',
             'site_name': '示例天气', 'date': '2026-11-25', 'snippet': '晴',
             'chunks': [{'text': '上海今天晴，最高 18 度'}]}]})
        self.assertEqual(outcome['count'], 1)
        entry = outcome['results'][0]
        self.assertEqual(entry['url'], 'https://weather.example.com/sh')
        self.assertEqual(entry['title'], '上海天气预报')
        self.assertEqual(entry['site'], '示例天气')
        self.assertIn('最高 18 度', entry['content'])
        self.assertTrue(entry['snippet'])

    def test_empty_results_are_explicit(self):
        outcome = self._run({'search_results': []})
        self.assertEqual(outcome['count'], 0)
        self.assertIn('note', outcome)

    def test_http_error_is_structured(self):
        outcome = self._run({'error': 'boom'}, status_code=500)
        self.assertIn('error', outcome)
        self.assertNotIn('results', outcome)

    def test_tool_requires_query(self):
        result = registry.execute_tool('search_web', {}, registry.DIALOGUE, 'allowed', {})
        self.assertIn('error', result['result'])

    def test_tool_disabled_without_provider_support(self):
        with patch.dict(os.environ, {'MINIMAX_LOCAL_SEARCH': ''}):
            names = [s['name'] for s in registry.schemas_for('dialogue', 'allowed')]
            self.assertNotIn('search_web', names)
        with patch.dict(os.environ, {'MINIMAX_LOCAL_SEARCH': '1'}):
            names = [s['name'] for s in registry.schemas_for('dialogue', 'allowed')]
            self.assertIn('search_web', names)

    def test_tool_returns_results_to_model(self):
        payload = {'search_results': [{'title': '标题', 'url': 'https://a.example.com/',
                                       'snippet': '片段'}]}
        with patch.dict(os.environ, {'MINIMAX_API_KEY': 'test-key'}), \
                patch('requests.post', return_value=_FakeSearchResponse(payload)):
            result = registry.execute_tool('search_web', {'query': '天气'}, registry.DIALOGUE,
                                           'allowed', {})
        self.assertTrue(result['allowed'])
        self.assertEqual(result['result']['count'], 1)
        self.assertEqual(result['result']['results'][0]['url'], 'https://a.example.com/')


class LoopSearchSourceTests(unittest.TestCase):
    """loop：本地搜索结果进网页来源卡；超限按次拒绝（W19）。"""

    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        with db.session_scope() as s:
            self.pet_id = s.execute(select(Pet)).scalars().one().id
        self.context = {'pet_id': self.pet_id, 'user_id': self.uid, 'text': '上海今天天气如何？',
                        'target': 'auto', 'history': [],
                        'snapshot': {'sections': {}, 'meta': {'chars': 10, 'trimmed': [], 'degraded': []}},
                        'now_iso': iso_z(utc_now())}
        self.stance = '{"scope":"allowed","intent":"knowledge","speaker":"assistant"}\n'

    def _turn(self, text='', calls=()):
        return {'text': self.stance + text, 'function_calls': list(calls),
                'web_search_calls': [], 'citations': [], 'usage': {}}

    def test_search_result_becomes_web_source(self):
        call = {'name': 'search_web', 'call_id': 'call_1',
                'arguments': json.dumps({'query': '上海天气'})}
        with patch.dict(os.environ, {'MINIMAX_LOCAL_SEARCH': '1'}), \
                patch('petsense.agent.model.respond',
                      side_effect=[self._turn(calls=[call]),
                                   self._turn('上海今天晴。')]) as respond, \
                patch('petsense.agent.registry.execute_tool') as execute:
            execute.return_value = {'allowed': True, 'reason': None, 'duration_ms': 1,
                                     'result': {'query': '上海天气', 'count': 1,
                                                'results': [{'title': '上海天气预报',
                                                             'url': 'https://weather.example.com/sh',
                                                             'snippet': '晴'}]}}
            outcome = loop.run_dialogue(dict(self.context))
        tool_names = [s['name'] for s in respond.call_args_list[0].kwargs['tools']]
        self.assertIn('search_web', tool_names)
        web_sources = [s for s in outcome['sources'] if s.get('type') == 'web']
        self.assertEqual(len(web_sources), 1)
        self.assertEqual(web_sources[0]['url'], 'https://weather.example.com/sh')
        self.assertEqual(web_sources[0]['title'], '上海天气预报')

    def test_search_budget_rejects_extra_calls(self):
        call = {'name': 'search_web', 'call_id': 'call_1',
                'arguments': json.dumps({'query': '上海天气'})}
        with patch.dict(os.environ, {'MINIMAX_LOCAL_SEARCH': '1'}), \
                patch('petsense.agent.model.respond',
                      side_effect=[self._turn('查一下。', calls=[call]),
                                   self._turn('再查一次。', calls=[call]),
                                   self._turn('再查一次。', calls=[call]),
                                   self._turn('好了。')]), \
                patch('petsense.agent.registry.execute_tool') as execute:
            execute.return_value = {'allowed': True, 'reason': None, 'duration_ms': 1,
                                     'result': {'query': '上海天气', 'count': 0, 'results': []}}
            outcome = loop.run_dialogue(dict(self.context))
        summary = outcome['trace']['tool_summary']
        reasons = [entry['reason'] for entry in summary if entry['name'] == 'search_web']
        self.assertIn('tool_budget_exceeded', reasons, '第三次搜索应按限次拒绝')
        self.assertEqual(execute.call_count, 2, '超限的那次不应真的调用供应商')


class StanceLayoutToleranceTests(unittest.TestCase):
    """W19：模型把 stance JSON 与正文写在同一行时，也要能剥离（不漏给用户）。

    Kimi 常输出 `{"scope":...}【照料助手】正文`，而原实现要求 stance 独占首行；
    漏剥会让原始 JSON 出现在气泡里，并且范围声明门控失效（stance=None）。
    """

    SAME_LINE = ('{"scope":"allowed","intent":"social","speaker":"assistant"}'
                 '【照料助手】你好呀。')
    OWN_LINE = '{"scope":"allowed","intent":"social","speaker":"assistant"}\n【爱宠】汪！'

    def test_parse_stance_same_line(self):
        stance, body = loop.parse_stance(self.SAME_LINE)
        self.assertIsNotNone(stance, '同行写法也要解析出 stance（否则范围门控失效）')
        self.assertEqual(stance['scope'], 'allowed')
        self.assertEqual(stance['intent'], 'social')
        self.assertEqual(body, '【照料助手】你好呀。')

    def test_parse_stance_own_line_unchanged(self):
        stance, body = loop.parse_stance(self.OWN_LINE)
        self.assertEqual(stance['scope'], 'allowed')
        self.assertEqual(body, '【爱宠】汪！')

    def test_non_stance_json_untouched(self):
        text = '{"answer": 42}'
        stance, body = loop.parse_stance(text)
        self.assertIsNone(stance)
        self.assertEqual(body, text)

    def test_delta_filter_strips_same_line(self):
        seen = []
        stream = loop.DeltaFilter(seen.append)
        stream.feed(self.SAME_LINE)
        stream.close()
        self.assertTrue(seen, '正文应被放行')
        self.assertNotIn('"scope"', seen[-1], '声明不得出现在流式正文里')
        self.assertEqual(seen[-1], '【照料助手】你好呀。')

    def test_delta_filter_partial_json_waits(self):
        seen = []
        stream = loop.DeltaFilter(seen.append)
        stream.feed('{"scope":"allowed","intent":"so')
        self.assertEqual(seen, [], 'JSON 未收全时不放行')
        stream.feed(self.SAME_LINE)
        stream.close()
        self.assertNotIn('"scope"', seen[-1])

    def test_role_delta_filter_strips_same_line(self):
        seen = []
        stream = loop.RoleDeltaFilter(seen.append, ['爱宠', '照料助手'])
        stream.feed(self.SAME_LINE)
        stream.close()
        self.assertNotIn('"scope"', seen[-1])
        self.assertEqual(seen[-1], '你好呀。')

    def test_loop_reply_has_no_json(self):
        class _Case(unittest.TestCase):
            pass
        case = _Case()
        fresh_db(case)
        uid = seed_user_with_pet()
        with db.session_scope() as s:
            pet_id = s.execute(select(Pet)).scalars().one().id
        context = {'pet_id': pet_id, 'user_id': uid, 'text': '你好', 'target': 'auto',
                   'history': [], 'snapshot': {'sections': {},
                                               'meta': {'chars': 10, 'trimmed': [], 'degraded': []}},
                   'now_iso': iso_z(utc_now())}
        turn = {'text': self.SAME_LINE, 'function_calls': [], 'web_search_calls': [],
                'citations': [], 'usage': {}}
        with patch('petsense.agent.model.respond', return_value=turn):
            outcome = loop.run_dialogue(dict(context))
        self.assertNotIn('"scope"', outcome['reply'], '最终回复不得含原始声明 JSON')
        self.assertEqual(outcome['reply'], '【照料助手】你好呀。')
        self.assertEqual(outcome['stance']['scope'], 'allowed')


if __name__ == '__main__':
    unittest.main()
