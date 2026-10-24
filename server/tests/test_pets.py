import sys
import unittest
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import AuthSession, Pet, Reading, User
from petsense.pets import routes as pets_routes
from helpers import fresh_db


def seed_user_with_pet(device_id='esp32-001'):
    from petsense.db.models import Device
    with db.session_scope() as s:
        user = User(username='u1', display_name='u1', password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        s.add(Pet(user_id=user.id, name='Max', species='dog', breed='', avatar_emoji='🐾',
                  device_id=device_id, created_at=db.utc_now()))
        if device_id:
            s.add(Device(device_id=device_id, owner_id=user.id, name='',
                         claimed_at=db.utc_now()))
        return user.id


class PetsTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(pets_routes.bp)
        self.client = app.test_client()

    def register_and_header(self):
        token = secrets.token_urlsafe(16)
        uid = seed_user_with_pet()
        with db.session_scope() as s:
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=uid, expires_at=db.utc_now() + timedelta(days=1)))
        return {'X-Petsense-Session': token}

    def test_pets_list_shape(self):
        headers = self.register_and_header()
        reply = self.client.get('/api/app/pets', headers=headers).get_json()
        pet = reply['pets'][0]
        self.assertEqual(pet['name'], 'Max')
        self.assertIn('created_at', pet)
        self.assertIn('avatarImageUrl', pet)
        self.assertIsNone(pet['batteryPercent'])  # 无遥测数据

    def test_create_pet_requires_login_and_name(self):
        reply = self.client.post('/api/app/pets', json={'name': 'Nick'})
        self.assertEqual(reply.status_code, 401)
        headers = self.register_and_header()
        bad = self.client.post('/api/app/pets', headers=headers, json={'name': ''})
        self.assertEqual(bad.status_code, 400)
        # 一户一宠：setUp 已为该用户建过宠物，第二只被拒
        second = self.client.post('/api/app/pets', headers=headers,
                                  json={'name': 'Nick', 'species': 'cat', 'deviceId': 'esp32-002'})
        self.assertEqual(second.status_code, 409)

    def test_dashboard_merges_telemetry(self):
        headers = self.register_and_header()
        with db.session_scope() as s:
            s.add(Reading(topic='petsense/sensor/esp32-001',
                          payload='{"health":{"valid":true,"heartRate":120,"spo2":98,"bodyTemp":38.5},"battery":{"percent":77}}',
                          received_at=db.utc_now()))
        reply = self.client.get('/api/app/dashboard', headers=headers).get_json()
        self.assertEqual(reply['latest']['heartRate'], 120)
        self.assertTrue(reply['latest']['online'])
        self.assertRegex(reply['latest']['receivedAt'], r'Z$')
        self.assertEqual(reply['pet']['name'], 'Max')

    def test_uwb_latest_scoped_to_my_device(self):
        from petsense import stats as stats_module
        headers = self.register_and_header()
        stats_module.last_uwb_frames.clear()
        self.assertIsNone(self.client.get('/api/app/uwb/latest',
                                          headers=headers).get_json()['frame'])
        stats_module.last_uwb_frames['esp32-001'] = {
            'payload': {'ts_ms': 1, 'uwb': {'valid': True}},
            'received_at': '2026-10-25T00:00:00.000Z', 'deviceId': 'esp32-001'}
        stats_module.last_uwb_frames['other-dev'] = {
            'payload': {'ts_ms': 9, 'uwb': {'valid': True}},
            'received_at': '2026-10-25T00:00:09.000Z', 'deviceId': 'other-dev'}
        frame = self.client.get('/api/app/uwb/latest', headers=headers).get_json()['frame']
        self.assertEqual(frame['deviceId'], 'esp32-001')
        self.assertEqual(self.client.get('/api/app/uwb/latest').status_code, 401)


if __name__ == '__main__':
    unittest.main()
