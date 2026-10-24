import sys
import unittest
import hashlib
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import config, db
from petsense.db.models import AuthSession, Device, User
from petsense.telemetry import commands as commands_module
from helpers import fresh_db


def make_user_with_device(username, device_id=None):
    with db.session_scope() as s:
        user = User(username=username, display_name=username, password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        if device_id:
            s.add(Device(device_id=device_id, owner_id=user.id, name='', claimed_at=db.utc_now()))
        return user.id


class CommandAuthTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        commands_module.audio_recording_states.clear()
        commands_module.audio_stop_timers.clear()
        app = Flask(__name__)
        app.register_blueprint(commands_module.bp)
        self.client = app.test_client()

    def tearDown(self):
        for timer in commands_module.audio_stop_timers.values():
            timer.cancel()
        commands_module.audio_recording_states.clear()
        commands_module.audio_stop_timers.clear()

    def seed(self, username, device_id=None):
        with db.session_scope() as s:
            user = User(username=username, display_name=username, password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user)
            s.flush()
            if device_id:
                s.add(Device(device_id=device_id, owner_id=user.id, name='',
                             claimed_at=db.utc_now()))
            return user.id

    def token(self, user_id):
        token = secrets.token_urlsafe(16)
        with db.session_scope() as s:
            s.add(AuthSession(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user_id,
                expires_at=db.utc_now() + timedelta(days=1),
            ))
        return token

    def test_requires_login_and_device(self):
        self.assertEqual(self.client.post('/api/audio-recording',
                         json={'enabled': True}).status_code, 401)
        uid = self.seed('nobody')
        token = self.token(uid)
        reply = self.client.post('/api/audio-recording',
                                 headers={'X-Petsense-Session': token}, json={'enabled': True})
        self.assertEqual(reply.status_code, 400)
        self.assertIn('请先绑定项圈', reply.get_json()['message'])

    def test_command_publishes_to_my_device_topic(self):
        uid = self.seed('alice', 'dev-a')
        token = self.token(uid)
        with patch.object(commands_module, 'publish_control_command',
                          return_value=('{}', True)) as publish:
            reply = self.client.post('/api/audio-recording',
                                     headers={'X-Petsense-Session': token},
                                     json={'enabled': True, 'durationSec': 2})
        self.assertTrue(reply.get_json()['published'])
        publish.assert_called_once()
        device_id, command = publish.call_args[0]
        self.assertEqual(device_id, 'dev-a')
        self.assertEqual(command['type'], 'record_audio_start')
        self.assertTrue(reply.get_json()['topic'].endswith('petsense/device/dev-a/command'))

    def test_experiment_state_isolated_per_device(self):
        uid_a = self.seed('alice', 'dev-a')
        uid_b = self.seed('bob', 'dev-b')
        token_a, token_b = self.token(uid_a), self.token(uid_b)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        with patch.object(config, 'CLOUD_DATA_DIR', holder.name), \
             patch.object(commands_module, 'publish_control_command', return_value=('{}', True)):
            self.client.post('/api/experiment', headers={'X-Petsense-Session': token_a},
                             json={'action': 'start', 'experimentId': 'exp-a'})
            state_b = self.client.get('/api/experiment',
                                      headers={'X-Petsense-Session': token_b}).get_json()
            self.assertFalse(state_b['active'])
            state_a = self.client.get('/api/experiment',
                                      headers={'X-Petsense-Session': token_a}).get_json()
            self.assertTrue(state_a['active'])
            self.assertEqual(state_a['experimentId'], 'exp-a')


if __name__ == '__main__':
    unittest.main()
