import unittest
from datetime import timedelta
from unittest.mock import patch
from flask import Flask
from helpers import fresh_db
from test_pets import seed_user_with_pet
from petsense import db
from petsense.agent.routes import bp
from petsense.db.models import Pet, TelemetryMinute
from petsense.agent.observations import query
from sqlalchemy import select


class AgentTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.uid = seed_user_with_pet()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.auth = patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid}, None))
        route = patch('petsense.agent.model.decide', return_value={'scope': 'allowed', 'speaker': 'assistant'})
        route.start()
        self.addCleanup(route.stop)
        self.auth.start()
        self.addCleanup(self.auth.stop)
        with db.session_scope() as s:
            self.pet = s.execute(select(Pet)).scalar_one().id
        self.path = f'/api/app/agent/pets/{self.pet}/turns'

    def test_idempotency_and_restart_history(self):
        with patch('petsense.agent.model.complete', return_value='【爱宠】汪！') as complete:
            data = {'text': '你好', 'requestId': 'r1', 'target': 'pet'}
            first = self.client.post(self.path, json=data)
            second = self.client.post(self.path, json=data)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json, second.json)
            self.assertEqual(complete.call_count, 1)
        self.assertEqual(len(self.client.get(self.path).json['turns']), 1)

    def test_owner_scope_and_invalid_input(self):
        with patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid + 1}, None)):
            self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.client.post(self.path, json={'text': 'hi'}).status_code, 400)

    def test_failure_preserves_owner_message(self):
        with patch('petsense.agent.model.complete', side_effect=ValueError('missing key')):
            response = self.client.post(self.path, json={'text': '今天怎样', 'requestId': 'r2'})
        self.assertEqual(response.json['turn']['status'], 'failed')
        self.assertEqual(self.client.get(self.path).json['turns'][0]['text'], '今天怎样')

    def test_window_and_device_scope(self):
        end = db.utc_now().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=2)
        with db.session_scope() as s:
            for device, minute in [('esp32-001', start), ('esp32-001', end), ('other-device', start)]:
                s.add(TelemetryMinute(owner_id=self.uid, device_id=device, minute=minute,
                                      metrics='{}', actions='{"walking": 15}', observed_seconds=30, frames=30))
        with db.session_scope() as s:
            result = query(s, s.get(Pet, self.pet), start, end)
        self.assertEqual(result['minuteCount'], 1)
        self.assertEqual(result['actionSeconds']['walking'], 15)

    def test_conflicting_request_key_and_scope_gate(self):
        with patch('petsense.agent.model.complete', return_value='hello'):
            self.client.post(self.path, json={'text': '你好', 'requestId': 'same'})
            result = self.client.post(self.path, json={'text': '另一句话', 'requestId': 'same'})
        self.assertEqual(result.status_code, 409)
        with patch('petsense.agent.model.decide', return_value={'scope': 'out_of_scope', 'speaker': 'assistant'}), patch('petsense.agent.routes.query') as tool, patch('petsense.agent.model.complete') as complete:
            result = self.client.post(self.path, json={'text': '宠物商城代码', 'requestId': 'scope'})
        self.assertEqual(result.json['turn']['status'], 'complete')
        tool.assert_not_called()
        complete.assert_not_called()

    def test_pending_turn_recovers_as_interrupted(self):
        from petsense.db.models.agent import AgentTurn
        with db.session_scope() as s:
            s.add(AgentTurn(pet_id=self.pet, request_id='old', text='原话', target='auto',
                            status='pending', reply='', created_at=db.utc_now() - timedelta(minutes=6)))
        turn = self.client.get(self.path).json['turns'][0]
        self.assertEqual(turn['status'], 'failed')
        self.assertEqual(turn['text'], '原话')

    def test_invalid_time_and_topic_reference(self):
        url = f'/api/app/agent/pets/{self.pet}/observations'
        self.assertEqual(self.client.get(url, query_string={'start': '2026-01-01', 'end': '2026-01-02'}).status_code, 400)
        self.assertEqual(self.client.post(self.path, json={'text': 'hi', 'requestId': 'x', 'topicId': 99999}).status_code, 404)

    def test_learning_coverage_is_durable_versioned_and_deduplicated(self):
        from petsense.telemetry.observations import ingest_observation
        from petsense.agent.learning import progress
        start = db.utc_now() + timedelta(seconds=1)
        frame = {'deviceId': 'esp32-001', 'source': 'esp32_mlp', 'boot_id': 'learn',
                 'modelVersion': 'v1', 'action': {'label': 'walking', 'confidence': .9},
                 'sound': {'label': 'quiet', 'confidence': .9}}
        with db.session_scope() as s:
            self.assertTrue(ingest_observation(s, dict(frame, seq=1), start))
            self.assertTrue(ingest_observation(s, dict(frame, seq=2), start + timedelta(seconds=1)))
            self.assertFalse(ingest_observation(s, dict(frame, seq=2), start + timedelta(seconds=1)))
        with db.session_scope() as s:
            values = progress(s, s.get(Pet, self.pet))
            self.assertEqual({v['dimension'] for v in values}, {'actions', 'sounds'})
            self.assertTrue(all(v['observedSeconds'] == 1 and v['validDays'] == 0 for v in values))
            minutes = s.execute(select(TelemetryMinute)).scalars().all()
            self.assertTrue(all('actions:v1' in minute.source_versions for minute in minutes if minute.observed_seconds > 0))

    def test_model_change_does_not_reuse_old_learning_progress(self):
        from petsense.db.models.agent import AgentLearningDay
        from petsense.agent.learning import progress
        now = db.utc_now()
        with db.session_scope() as s:
            for day in range(7):
                s.add(AgentLearningDay(pet_id=self.pet, device_id='esp32-001', feature='actions',
                    version='old', day=f'2026-10-{day+1:02}', seconds=21600, slots='[0,1]',
                    last_seen_at=now - timedelta(days=10-day)))
            s.add(AgentLearningDay(pet_id=self.pet, device_id='esp32-001', feature='actions',
                version='new', day='2026-11-13', seconds=30, slots='[0]', last_seen_at=now))
        with db.session_scope() as s:
            values = progress(s, s.get(Pet, self.pet))
        self.assertEqual(values[0]['version'], 'new')
        self.assertEqual(values[0]['validDays'], 0)
        self.assertEqual(values[1]['validDays'], 7)

    def test_read_state_persists_and_is_owner_scoped(self):
        with patch('petsense.agent.model.complete', return_value='hello'):
            turn = self.client.post(self.path, json={'text': '你好', 'requestId': 'read'}).json['turn']
        url = f'/api/app/agent/pets/{self.pet}/read'
        first = self.client.post(url, json={'ids': [turn['id']]}).json['turns'][0]
        second = self.client.post(url, json={'ids': [turn['id']]}).json['turns'][0]
        self.assertIsNotNone(first['readAt'])
        self.assertEqual(first['readAt'], second['readAt'])
        with patch('petsense.agent.routes.require_app_user', return_value=({'id': self.uid+1}, None)):
            self.assertEqual(self.client.post(url, json={'ids': [turn['id']]}).status_code, 404)

    def test_unavailable_actions_do_not_claim_execution(self):
        for intent in ('observe', 'write', 'search'):
            with patch('petsense.agent.model.decide', return_value={'scope': 'allowed', 'speaker': 'assistant', 'intent': intent}), patch('petsense.agent.model.complete') as complete:
                response = self.client.post(self.path, json={'text': '请求', 'requestId': intent})
            self.assertEqual(response.json['turn']['status'], 'complete')
            self.assertEqual(response.json['turn']['sources'], [])
            complete.assert_not_called()
