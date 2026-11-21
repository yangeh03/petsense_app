"""G1 验收回归：历史问答结构化路由、窗口校验、全量事实聚合、失败细分与降级。"""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from flask import Flask
from helpers import fresh_db
from test_pets import seed_user_with_pet
from petsense import db
from petsense.agent import facts, routing
from petsense.agent.routes import bp
from petsense.agent.routing import SHANGHAI
from petsense.db.models import Pet, TelemetryEvent, TelemetryMinute
from petsense.db.models.agent import AgentTurn
from petsense.agent.observations import query
from sqlalchemy import select


def shanghai_moment(days_ago=0, hour=8, minute=0):
    """上海时区的过去某天某时刻，返回 aware UTC。"""
    local = (datetime.now(SHANGHAI) - timedelta(days=days_ago)).replace(hour=hour, minute=minute,
                                                                        second=0, microsecond=0)
    return local.astimezone(timezone.utc)


class AgentHistoryTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.auth = patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid}, None))
        route = patch('petsense.agent.model.decide',
                      return_value={'scope': 'allowed', 'speaker': 'assistant', 'intent': 'read'})
        route.start()
        self.addCleanup(route.stop)
        self.auth.start()
        self.addCleanup(self.auth.stop)
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.path = f'/api/app/agent/pets/{self.pet}/turns'

    def seed_minute(self, moment, walking=30, observed=60):
        with db.session_scope() as s:
            s.add(TelemetryMinute(owner_id=self.uid, device_id='esp32-001', minute=moment.replace(second=0, microsecond=0),
                                  metrics='{"heartRate": {"sum": 120, "count": 2, "min": 58, "max": 62}}',
                                  actions=json.dumps({'walking': walking} if walking else {}),
                                  observed_seconds=observed, frames=observed))

    def seed_events(self, count, label, experimental, moment):
        with db.session_scope() as s:
            for index in range(count):
                s.add(TelemetryEvent(owner_id=self.uid, device_id='esp32-001', boot_id='b', model_version='v1',
                                     label=label, started_at=moment + timedelta(seconds=index),
                                     last_seen_at=moment + timedelta(seconds=index + 2),
                                     frames=2, confidence_sum=1.8, observed_seconds=3,
                                     experimental=1 if experimental else 0))

    def yesterday_window(self):
        now = datetime.now(SHANGHAI)
        start = routing.resolve_window({'start': '昨天', 'end': '今天'}, now)
        return start['start'], start['end']

    def complete_calling(self, *calls, error=None):
        """模拟模型：先执行给定的工具调用，再返回固定文本（或抛错）。"""
        def fake(messages, executor=None, on_delta=None):
            for arguments in calls:
                if executor:
                    executor(arguments)
            if error is not None:
                raise error
            return '模型的话'
        return fake

    # ── 结构化路由 ──

    def test_looks_like_history_guard(self):
        for text in ('帮我联网查一下它昨天走动多久', '它今天睡得怎么样', '11月19日它叫了几次',
                     '对比一下19号和20号的活动量'):
            self.assertTrue(routing.looks_like_history(text), text)
        for text in ('写一段宠物商城的代码', '你好呀', '金毛和柯基哪个好养', '帮我留意它晚上叫不叫'):
            self.assertFalse(routing.looks_like_history(text), text)

    def test_search_intent_with_data_question_goes_to_tool(self):
        with patch('petsense.agent.model.decide',
                   return_value={'scope': 'allowed', 'speaker': 'assistant', 'intent': 'search'}), \
             patch('petsense.agent.model.complete', side_effect=self.complete_calling({'start': '昨天', 'end': '今天', 'label': '昨天'})):
            self.seed_minute(shanghai_moment(days_ago=1, hour=8))
            response = self.client.post(self.path, json={'text': '帮我联网查一下它昨天走动多久', 'requestId': 's1'})
        turn = response.json['turn']
        self.assertEqual(turn['status'], 'complete')
        self.assertNotIn('联网核查还没开放', turn['reply'])
        start, end = self.yesterday_window()
        self.assertEqual(turn['sources'][0]['startAt'], db.iso_z(start))
        self.assertEqual(turn['sources'][0]['endAt'], db.iso_z(end))
        self.assertTrue(turn['reply'].startswith('【照料助手】'))

    def test_common_read_uses_database_without_model_text(self):
        self.seed_minute(shanghai_moment(days_ago=1, hour=8))
        with patch('petsense.agent.model.decide',
                   return_value={'scope': 'allowed', 'speaker': 'pet', 'intent': 'search'}), \
             patch('petsense.agent.model.complete') as generate:
            response = self.client.post(self.path, json={
                'text': '请查昨天的覆盖和走动秒数，附上 App 记录来源。',
                'requestId': 'verified-history',
            })
        turn = response.json['turn']
        self.assertEqual(turn['speaker'], 'assistant')
        self.assertIn('60.0 秒', turn['reply'])
        self.assertIn('30.0 秒', turn['reply'])
        self.assertEqual(len(turn['sources']), 1)
        generate.assert_not_called()

    def test_unqueried_history_text_has_no_authority(self):
        with patch('petsense.agent.model.decide',
                   return_value={'scope': 'allowed', 'speaker': 'assistant', 'intent': 'read'}), \
             patch('petsense.agent.model.complete', return_value='记录到999秒走动。'):
            response = self.client.post(self.path, json={
                'text': '请查那段走动记录', 'requestId': 'unqueried-history',
            })
        turn = response.json['turn']
        self.assertNotIn('999', turn['reply'])
        self.assertEqual(turn['sources'], [])

    # ── 窗口校验 ──

    def test_window_resolution_shanghai_rules(self):
        now = datetime(2026, 11, 20, 14, 30, tzinfo=SHANGHAI)
        cases = [
            ({'start': '昨天', 'end': '今天'}, '2026-11-19T00:00:00+08:00', '2026-11-20T00:00:00+08:00'),
            ({'start': '2026-11-19'}, '2026-11-19T00:00:00+08:00', '2026-11-20T00:00:00+08:00'),
            ({'start': '前天'}, '2026-11-18T00:00:00+08:00', '2026-11-19T00:00:00+08:00'),
            ({'start': '最近7天', 'end': '明天'}, '2026-11-14T00:00:00+08:00', '2026-11-21T00:00:00+08:00'),
            ({'start': '2026-11-01T08:00:00', 'end': '2026-11-01T20:00:00'}, '2026-11-01T08:00:00+08:00', '2026-11-01T20:00:00+08:00'),
        ]
        for spec, start, end in cases:
            window = routing.resolve_window(spec, now)
            self.assertEqual(window['start'].isoformat(), start, spec)
            self.assertEqual(window['end'].isoformat(), end, spec)
        for spec in ({'start': '2026-06-01', 'end': '2026-11-20'}, {'start': '今天', 'end': '昨天'},
                     {'start': '昨天晚上八点'}, {'start': '2026-11-19T08:00:00'}):
            with self.assertRaises(ValueError, msg=spec):
                routing.resolve_window(spec, now)

    # ── 全量事实聚合 ──

    def test_sound_facts_aggregate_full_set_beyond_truncated_details(self):
        moment = shanghai_moment(days_ago=1, hour=10)
        self.seed_events(140, 'whine', True, moment)
        self.seed_events(1031, 'bark', False, moment)
        self.seed_minute(moment)
        start, end = self.yesterday_window()
        with db.session_scope() as s:
            result = query(s, s.get(Pet, self.pet), start, end)
        self.assertEqual(result['soundEventCount'], 1171)
        self.assertEqual(result['soundEventsReturned'], 200)
        self.assertTrue(result['soundEventsTruncated'])
        breakdown = {item['label']: item for item in result['soundBreakdown']}
        self.assertEqual(breakdown['whine'], {'label': 'whine', 'count': 140, 'experimental': 140})
        self.assertEqual(breakdown['bark'], {'label': 'bark', 'count': 1031, 'experimental': 0})
        view = facts.model_view({'label': '昨天', 'start': start, 'end': end, 'state': 'ok', 'result': result})
        rendered = json.dumps(view, ensure_ascii=False)
        self.assertNotIn('esp32', rendered)
        self.assertNotIn('deviceId', rendered)
        self.assertEqual(view['声音事件总数'], 1171)
        self.assertIn('明细仅返回前200条', view['声音明细说明'])
        stats = {item['类型']: item for item in view['声音类型全量统计']}
        self.assertEqual(stats['呜咽']['数量'], 140)
        self.assertEqual(stats['呜咽']['其中实验性标注'], 140)
        self.assertEqual(stats['吠叫']['数量'], 1031)
        from petsense.agent.history_answer import answer
        reply = answer('这批声音事件的明细占总数的百分之多少？', [
            {'start': start, 'end': end, 'state': 'ok', 'result': result},
        ], 'assistant')
        self.assertIn('1171 条', reply)
        self.assertIn('200 条', reply)
        self.assertIn('17.1%', reply)

    def test_zero_coverage_is_not_reported_as_zero_reading(self):
        start, end = self.yesterday_window()
        with db.session_scope() as s:
            result = query(s, s.get(Pet, self.pet), start, end)
        record = {'label': '昨天', 'start': start, 'end': end, 'state': 'no_coverage', 'result': result}
        view = facts.model_view(record)
        self.assertEqual(view['状态'], '窗口内没有记录')
        self.assertNotIn('覆盖秒数', view)
        self.assertNotIn('姿态秒数', view)
        summary = facts.template_summary([record])
        self.assertIn('没有记录', summary)
        self.assertNotIn('覆盖约0', summary)
        failed = {'label': '昨天', 'start': start, 'end': end, 'state': 'query_failed', 'result': None}
        self.assertEqual(facts.model_view(failed)['状态'], '查询失败')
        # 降级模板：可用的 ok 事实与失败窗口并存时，两段都如实区分。
        self.seed_minute(shanghai_moment(days_ago=0, hour=7))
        now = datetime.now(SHANGHAI)
        today = routing.resolve_window({'start': '今天', 'end': '明天'}, now)
        with db.session_scope() as s:
            ok_result = query(s, s.get(Pet, self.pet), today['start'], today['end'])
        ok_record = {'label': '今天', 'start': today['start'], 'end': today['end'], 'state': 'ok', 'result': ok_result}
        mixed = facts.template_summary([ok_record, failed])
        self.assertIn('覆盖约60.0秒', mixed)
        self.assertIn('没查成', mixed)

    # ── 主题追问复用来源窗口 ──

    def test_followup_reuses_replyto_source_window(self):
        start, end = self.yesterday_window()
        source = {'type': 'observations', 'versions': [], 'deviceId': 'esp32-001',
                  'startAt': db.iso_z(start), 'endAt': db.iso_z(end)}
        with db.session_scope() as s:
            turn = AgentTurn(pet_id=self.pet, request_id='root-1', text='它昨天走动多久', target='auto',
                             reply='【照料助手】昨天覆盖60秒。', sources=json.dumps([source]),
                             status='complete', created_at=db.utc_now())
            s.add(turn)
            s.flush()
            root_id = turn.id
        executor_args = []

        def fake(messages, executor=None, on_delta=None):
            if executor:
                executor_args.append(None)
                executor({})
            return '呜咽140条都是实验性标注。'

        with patch('petsense.agent.model.complete', side_effect=fake):
            response = self.client.post(self.path, json={'text': '那实验性的占多少', 'requestId': 'fu1', 'replyTo': root_id})
        turn = response.json['turn']
        self.assertEqual(turn['status'], 'complete')
        self.assertEqual(len(turn['sources']), 1)
        self.assertEqual(turn['sources'][0]['startAt'], db.iso_z(start))
        self.assertEqual(turn['reply'].startswith('【照料助手】'), True)

    def test_retry_with_topic_only_still_finds_window(self):
        start, end = self.yesterday_window()
        source = {'type': 'observations', 'versions': [], 'deviceId': 'esp32-001',
                  'startAt': db.iso_z(start), 'endAt': db.iso_z(end)}
        with db.session_scope() as s:
            turn = AgentTurn(pet_id=self.pet, request_id='root-2', text='它昨天走动多久', target='auto',
                             reply='【照料助手】昨天覆盖60秒。', sources=json.dumps([source]),
                             status='complete', created_at=db.utc_now())
            s.add(turn)
            s.flush()
            root_id = turn.id
        with patch('petsense.agent.model.complete', side_effect=self.complete_calling({})):
            response = self.client.post(self.path, json={'text': '再说一次细分', 'requestId': 'retry1', 'topicId': root_id})
        self.assertEqual(len(response.json['turn']['sources']), 1)

    def test_history_without_window_or_reference_asks_for_clarification(self):
        captured = {}

        def fake(messages, executor=None, on_delta=None):
            captured['tool'] = executor({}) if executor else None
            return '你想看哪个时段的记录？'

        with patch('petsense.agent.model.complete', side_effect=fake):
            response = self.client.post(self.path, json={'text': '它最近怎么样', 'requestId': 'cl1'})
        self.assertEqual(response.json['turn']['status'], 'complete')
        self.assertEqual(response.json['turn']['sources'], [])
        self.assertEqual(captured['tool'].get('error'), '没有可查询的时间窗口')

    # ── 失败细分与稳健降级 ──

    def test_degradation_answers_from_gathered_facts_when_model_fails(self):
        self.seed_minute(shanghai_moment(days_ago=1, hour=8))
        with patch('petsense.agent.model.complete', side_effect=self.complete_calling(
                {'start': '昨天', 'end': '今天', 'label': '昨天'}, error=ValueError('模型未返回可用回复'))):
            with self.assertLogs(self.app.logger, level='WARNING') as logs:
                response = self.client.post(self.path, json={'text': '它昨晚走动多久', 'requestId': 'dg1'})
        turn = response.json['turn']
        self.assertEqual(turn['status'], 'complete')
        self.assertIn('覆盖约60.0秒', turn['reply'])
        self.assertIn('走动约30秒', turn['reply'])
        self.assertEqual(len(turn['sources']), 1)
        joined = '\n'.join(logs.output)
        self.assertIn('failed:', joined)
        self.assertNotIn('它昨晚走动多久', joined)  # 正文与秘密不进日志

    def test_model_failure_carries_phase_round_status_code(self):
        import requests as requests_lib

        class FakeResponse:
            status_code = 503

            def json(self):
                return {'base_resp': {'status_code': 1027, 'status_msg': 'rate limited'}}

        error = requests_lib.exceptions.HTTPError('503', response=FakeResponse())
        with patch.dict(os.environ, {'MINIMAX_API_KEY': 'test-key'}), \
             patch('petsense.agent.model.requests.post', side_effect=error):
            with self.assertRaises(ValueError) as ctx:
                from petsense.agent import model
                model.complete([{'role': 'user', 'content': 'hi'}])
        self.assertIsInstance(ctx.exception, model.ModelFailure)
        self.assertEqual(ctx.exception.failure_info,
                         {'phase': 'request', 'round': 0, 'status': 503, 'code': 1027,
                          'finish': None, 'detail': '模型请求失败'})

    def test_empty_final_reply_reports_finish_reason(self):
        class FakeResponse:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def raise_for_status(self):
                return None

            def json(self):
                return {'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': ''}}]}

        with patch.dict(os.environ, {'MINIMAX_API_KEY': 'test-key'}), \
             patch('petsense.agent.model.requests.post', return_value=FakeResponse()):
            with self.assertRaises(ValueError) as ctx:
                from petsense.agent import model
                model.complete([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(ctx.exception.failure_info['phase'], 'final_content')
        self.assertEqual(ctx.exception.failure_info['finish'], 'length')

    # ── 角色一致性 ──

    def test_reply_marker_matches_persisted_speaker(self):
        with patch('petsense.agent.model.complete', return_value='【爱宠】汪！今天走了很多步。'):
            response = self.client.post(self.path, json={'text': '它昨天走动多久', 'requestId': 'sp1'})
        turn = response.json['turn']
        self.assertEqual(turn['speaker'], 'assistant')
        self.assertTrue(turn['reply'].startswith('【照料助手】'))

    def test_auto_social_keeps_decision_speaker(self):
        with patch('petsense.agent.model.decide',
                   return_value={'scope': 'allowed', 'speaker': 'pet', 'intent': 'social'}), \
             patch('petsense.agent.model.complete', return_value='我是你的小可爱。'):
            response = self.client.post(self.path, json={'text': '你好呀', 'requestId': 'sp2'})
        turn = response.json['turn']
        self.assertEqual(turn['speaker'], 'pet')
        self.assertTrue(turn['reply'].startswith('【爱宠】'))

    def test_explicit_pet_target_wins_over_auto_assistant(self):
        with patch('petsense.agent.model.complete', return_value='汪。'):
            response = self.client.post(self.path, json={'text': '它昨天走动多久', 'requestId': 'sp3', 'target': 'pet'})
        turn = response.json['turn']
        self.assertEqual(turn['speaker'], 'pet')
        self.assertTrue(turn['reply'].startswith('【爱宠】'))
