"""Regression coverage for observation aggregation and model diagnostics."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from helpers import fresh_db
from test_pets import seed_user_with_pet
from petsense import db
from petsense.agent.observations import query
from petsense.db.models import Pet, TelemetryEvent


class AgentObservationTests(unittest.TestCase):
    def test_full_sound_counts_survive_the_detail_limit(self):
        fresh_db(self)
        owner = seed_user_with_pet()
        start = datetime(2026, 11, 18, tzinfo=timezone.utc)
        with db.session_scope() as session:
            for label, count, experimental in [('whine', 35, 1), ('bark', 1136, 0)]:
                for index in range(count):
                    moment = start + timedelta(seconds=index)
                    session.add(TelemetryEvent(
                        owner_id=owner, device_id='esp32-001', boot_id='acceptance', model_version='v1',
                        label=label, started_at=moment, last_seen_at=moment,
                        frames=1, confidence_sum=1.0, observed_seconds=0, experimental=experimental,
                    ))
        with db.session_scope() as session:
            pet = session.execute(select(Pet)).scalar_one()
            result = query(session, pet, start, start + timedelta(days=1))
        self.assertEqual(result['soundEventCount'], 1171)
        self.assertEqual(result['soundEventsReturned'], 200)
        self.assertTrue(result['soundEventsTruncated'])
        self.assertCountEqual(result['soundBreakdown'], [
            {'label': 'whine', 'count': 35, 'experimental': 35},
            {'label': 'bark', 'count': 1136, 'experimental': 0},
        ])


class AgentModelDiagnosticsTests(unittest.TestCase):
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
