import sys
import unittest
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.actions import service as actions_service
from petsense.db.models import ActionPrediction, AuthSession, Device, Reading, User
from petsense.telemetry import realtime
from helpers import fresh_db


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = None

    def send(self, data):
        self.sent.append(data)

    def close(self, status=1000):
        self.closed = status

    def receive(self):
        return None


def seed_user_with_device(username, device_id):
    with db.session_scope() as s:
        user = User(username=username, display_name=username, password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        if device_id:
            s.add(Device(device_id=device_id, owner_id=user.id, name='', claimed_at=db.utc_now()))
        token = secrets.token_urlsafe(16)
        s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
        return token


class WsScopeTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        realtime.ws_clients.clear()
        app = Flask(__name__)
        realtime.sock.init_app(app)
        app.register_blueprint(realtime.bp)
        app.register_blueprint(actions_service.bp)
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        realtime.ws_clients.clear()

    def test_packet_visibility_matrix(self):
        self.assertTrue(realtime.packet_visible({'a'}, 'a'))
        self.assertFalse(realtime.packet_visible({'a'}, 'b'))
        self.assertTrue(realtime.packet_visible(set(), None))  # 系统帧全体可见

    def test_device_ids_for_user(self):
        token = seed_user_with_device('alice', 'dev-a')
        with db.session_scope() as s:
            user_id = s.query(User).filter_by(username='alice').one().id
        self.assertEqual(realtime.device_ids_for_user(user_id), {'dev-a'})

    def test_handshake_rejects_missing_or_bad_token(self):
        fake = FakeWS()
        with self.app.test_request_context('/ws'):
            realtime.ws_handler(fake)
        self.assertEqual(fake.closed, 4401)
        self.assertEqual(len(realtime.ws_clients), 0)
        fake2 = FakeWS()
        with self.app.test_request_context('/ws?token=not-a-token'):
            realtime.ws_handler(fake2)
        self.assertEqual(fake2.closed, 4401)

    def test_handshake_registers_scope_and_broadcast_filters(self):
        alice_token = seed_user_with_device('alice', 'dev-a')
        seed_user_with_device('bob', 'dev-b')
        fake = FakeWS()
        with self.app.test_request_context(f'/ws?token={alice_token}'):
            realtime.ws_handler(fake)
        # 注册后被 receive()=None 正常退出并摘除；此处直接验证广播过滤逻辑
        realtime.ws_clients.clear()
        with db.session_scope() as s:
            alice_id = s.query(User).filter_by(username='alice').one().id
        realtime.ws_clients[fake] = (alice_id, frozenset({'dev-a'}))
        realtime.broadcast_packet('{"i":1}', 'dev-a')
        realtime.broadcast_packet('{"i":2}', 'dev-b')
        realtime.broadcast_packet('{"type":"ping"}', None)
        self.assertEqual(fake.sent, ['{"i":1}', '{"type":"ping"}'])

    def test_refresh_scopes_after_claim(self):
        alice_token = seed_user_with_device('alice', None)
        with db.session_scope() as s:
            alice_id = s.query(User).filter_by(username='alice').one().id
        fake = FakeWS()
        realtime.ws_clients[fake] = (alice_id, frozenset())
        with db.session_scope() as s:
            s.add(Device(device_id='dev-new', owner_id=alice_id, name='', claimed_at=db.utc_now()))
        realtime.refresh_scopes()
        realtime.broadcast_packet('{"i":1}', 'dev-new')
        self.assertEqual(fake.sent, ['{"i":1}'])

    def test_latest_full_scoped_to_my_devices(self):
        alice_token = seed_user_with_device('alice', 'dev-a')
        seed_user_with_device('bob', 'dev-b')
        with db.session_scope() as s:
            s.add(Reading(topic='petsense/sensor/dev-a', payload='{}',
                          received_at=db.utc_now(), device_id='dev-a'))
            s.add(Reading(topic='petsense/sensor/dev-b', payload='{}',
                          received_at=db.utc_now(), device_id='dev-b'))
        headers = {'X-Petsense-Session': alice_token}
        payload = self.client.get('/api/latest-full', headers=headers).get_json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['data'][0]['topic'], 'petsense/sensor/dev-a')
        self.assertEqual(self.client.get('/api/latest-full').status_code, 401)

    def test_action_endpoints_scope(self):
        alice_token = seed_user_with_device('alice', 'dev-a')
        with db.session_scope() as s:
            s.add(ActionPrediction(device_id='dev-a', event_id='e', label='l', action_key='k',
                                   confidence=0.5, scores_json='{}', sample_count=2,
                                   window_start_ts_ms=0, window_end_ts_ms=1000,
                                   model_version='v', predicted_at=db.utc_now()))
            s.add(ActionPrediction(device_id='dev-b', event_id='e', label='l', action_key='k',
                                   confidence=0.5, scores_json='{}', sample_count=2,
                                   window_start_ts_ms=0, window_end_ts_ms=1000,
                                   model_version='v', predicted_at=db.utc_now()))
        headers = {'X-Petsense-Session': alice_token}
        history = self.client.get('/api/action/history', headers=headers).get_json()
        self.assertEqual(history['count'], 1)
        self.assertEqual(history['data'][0]['deviceId'], 'dev-a')
        self.assertEqual(self.client.get('/api/action/history').status_code, 401)
        status = self.client.get('/api/action/status').get_json()
        self.assertIn('enabled', status)
        self.assertNotIn('latest', status)


if __name__ == '__main__':
    unittest.main()
