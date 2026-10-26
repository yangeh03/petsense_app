import sys
import unittest
import hashlib
import secrets
import io
import tempfile
from unittest.mock import patch
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import AuthSession, Pet, Reading, User
from petsense.pets import routes as pets_routes
from helpers import fresh_db
from PIL import Image


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

    def test_patch_profile_persists_name_without_changing_device(self):
        headers = self.register_and_header()
        pet_id = self.client.get('/api/app/pets', headers=headers).json['pets'][0]['id']
        result = self.client.patch(f'/api/app/pets/{pet_id}', headers=headers,
                                   json={'name': ' 小满 ', 'breed': '中华田园犬'})
        self.assertEqual(result.status_code, 200)
        dashboard = self.client.get('/api/app/dashboard', headers=headers).json
        self.assertEqual(dashboard['pet']['name'], '小满')
        self.assertEqual(dashboard['pet']['breed'], '中华田园犬')
        self.assertEqual(dashboard['pet']['device_id'], 'esp32-001')

    def test_patch_rejects_empty_names_and_device_reassignment(self):
        headers = self.register_and_header()
        pet_id = self.client.get('/api/app/pets', headers=headers).json['pets'][0]['id']
        for body in ({'name': ''}, {'name': 'n' * 21}, {'species': 'lion'},
                     {'name': 123}, {'deviceId': 'other'}, []):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch(f'/api/app/pets/{pet_id}',
                                 headers=headers, json=body).status_code, 400)

    def test_patch_requires_owner_and_login(self):
        from test_devices import seed_user, make_session_token
        headers = self.register_and_header()
        pet_id = self.client.get('/api/app/pets', headers=headers).json['pets'][0]['id']
        self.assertEqual(self.client.patch(f'/api/app/pets/{pet_id}', json={'name': 'x'}).status_code, 401)
        other = {'X-Petsense-Session': make_session_token(seed_user('pet-other'))}
        self.assertEqual(self.client.patch(f'/api/app/pets/{pet_id}', headers=other,
                                          json={'name': 'x'}).status_code, 404)

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

    def owned(self):
        headers = self.register_and_header()
        pet = self.client.get('/api/app/pets', headers=headers).get_json()['pets'][0]
        return headers, pet

    def photo(self):
        data = io.BytesIO()
        Image.new('RGB', (8, 8), 'green').save(data, format='PNG')
        data.seek(0)
        return data, 'pet.png'

    def test_profile_rename_is_persistent_and_keeps_device(self):
        headers, pet = self.owned()
        url = f'/api/app/pets/{pet["id"]}'
        self.assertEqual(self.client.get(url, headers=headers).status_code, 200)
        reply = self.client.patch(url, headers=headers, json={
            'name': '  小团子 🐶  ', 'breed': ' 柴犬 ', 'species': 'dog',
            'avatarEmoji': '🐶', 'avatarMode': 'keep'})
        self.assertEqual(reply.status_code, 200)
        saved = reply.get_json()['pet']
        self.assertEqual(saved['name'], '小团子 🐶')
        self.assertEqual(saved['breed'], '柴犬')
        self.assertEqual(saved['device_id'], pet['device_id'])
        self.assertEqual(self.client.get(url, headers=headers).get_json()['pet']['name'], '小团子 🐶')
        dashboard = self.client.get('/api/app/dashboard', headers=headers).get_json()
        self.assertEqual(dashboard['pet']['name'], saved['name'])
        # Retry after a lost response does not duplicate or detach the pet.
        self.assertEqual(self.client.patch(url, headers=headers, json={'name': saved['name']}).status_code, 200)
        self.assertEqual(len(self.client.get('/api/app/pets', headers=headers).get_json()['pets']), 1)

    def test_profile_strict_validation_and_unicode_boundaries(self):
        headers, pet = self.owned()
        url = f'/api/app/pets/{pet["id"]}'
        for payload in [{'name': ''}, {'name': '  '}, {'name': None}, {'name': []},
                        {'name': '🐶' * 21}, {'species': 'bird'}, {'breed': '中' * 41},
                        {'breed': None}, {'device_id': 'stolen'}, {'user_id': 42},
                        {'avatarMode': 'bad'}, {'avatarEmoji': '任意文本'}, [], None]:
            with self.subTest(payload=payload):
                reply = self.client.patch(url, headers=headers, json=payload,
                                          content_type='application/json')
                self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.client.patch(url, headers=headers,
                                          json={'name': '🐶' * 20, 'breed': '中' * 40}).status_code, 200)

    def test_profile_requires_ownership_before_processing_upload(self):
        headers, pet = self.owned()
        url = f'/api/app/pets/{pet["id"]}'
        self.assertEqual(self.client.patch(url, json={'name': 'No'}).status_code, 401)
        self.assertEqual(self.client.get(url).status_code, 401)
        token = secrets.token_urlsafe(16)
        with db.session_scope() as s:
            user = User(username='other', display_name='other', password_hash='x', salt='s',
                        avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(user)
            s.flush()
            s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
        stranger = {'X-Petsense-Session': token}
        self.assertEqual(self.client.get(url, headers=stranger).status_code, 404)
        reply = self.client.patch(url, headers=stranger, data={'avatarMode': 'photo', 'photo': self.photo()})
        self.assertEqual(reply.status_code, 404)

    def test_profile_photo_replace_keep_and_default(self):
        headers, pet = self.owned()
        url = f'/api/app/pets/{pet["id"]}'
        with tempfile.TemporaryDirectory() as folder, patch.object(pets_routes.config, 'UPLOADS_DIR', folder):
            reply = self.client.patch(url, headers=headers,
                                      data={'name': '小白', 'avatarMode': 'photo', 'photo': self.photo()})
            self.assertEqual(reply.status_code, 200)
            photo_url = reply.get_json()['pet']['avatarImageUrl']
            self.assertTrue(photo_url.startswith('/uploads/'))
            saved = self.client.patch(url, headers=headers, json={'breed': '田园犬'}).get_json()['pet']
            self.assertEqual(saved['avatarImageUrl'], photo_url)
            saved = self.client.patch(url, headers=headers,
                                      json={'avatarMode': 'emoji', 'avatarEmoji': '🐾'}).get_json()['pet']
            self.assertIsNone(saved['avatarImageUrl'])
            self.assertEqual(saved['avatar_emoji'], '🐾')

    def test_profile_bad_image_does_not_partially_save(self):
        headers, pet = self.owned()
        url = f'/api/app/pets/{pet["id"]}'
        for photo in [(io.BytesIO(b'\x89PNG broken'), 'fake.png'),
                      (io.BytesIO(b'x' * (10 * 1024 * 1024 + 1)), 'big.jpg')]:
            reply = self.client.patch(url, headers=headers,
                                      data={'name': '不应保存', 'avatarMode': 'photo', 'photo': photo})
            self.assertEqual(reply.status_code, 400)
            self.assertEqual(self.client.get(url, headers=headers).get_json()['pet']['name'], pet['name'])
        self.assertEqual(self.client.patch(url, headers=headers, json={'avatarMode': 'photo'}).status_code, 400)

    def test_profile_database_failure_removes_new_photo(self):
        import os
        from sqlalchemy import event
        headers, pet = self.owned()
        def fail_update(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE pets '):
                raise RuntimeError('simulated database failure')
        with tempfile.TemporaryDirectory() as folder, patch.object(pets_routes.config, 'UPLOADS_DIR', folder):
            engine = db.engine()
            event.listen(engine, 'before_cursor_execute', fail_update)
            try:
                with patch.object(self.client.application.logger, 'error'):
                    reply = self.client.patch(f'/api/app/pets/{pet["id"]}', headers=headers,
                                              data={'name': '不应保存', 'avatarMode': 'photo', 'photo': self.photo()})
                self.assertEqual(reply.status_code, 500)
            finally:
                event.remove(engine, 'before_cursor_execute', fail_update)
            self.assertEqual(os.listdir(folder), [])
            self.assertEqual(self.client.get('/api/app/pets', headers=headers).get_json()['pets'][0]['name'], pet['name'])

    def test_public_profile_includes_saved_avatar_without_private_fields(self):
        from petsense.social.routes import bp
        # Register before first request.
        self.client.application.register_blueprint(bp)
        headers, pet = self.owned()
        with tempfile.TemporaryDirectory() as folder, patch.object(pets_routes.config, 'UPLOADS_DIR', folder):
            reply = self.client.patch(f'/api/app/pets/{pet["id"]}', headers=headers,
                                      data={'name': '中文资料', 'avatarMode': 'photo', 'photo': self.photo()})
            self.assertEqual(reply.status_code, 200)
            saved = reply.get_json()['pet']
            public = self.client.get(f'/api/app/users/{pet["user_id"]}').get_json()['profile']['pets'][0]
            self.assertEqual(public['name'], saved['name'])
            self.assertEqual(public['avatarImageUrl'], saved['avatarImageUrl'])
            self.assertNotIn('user_id', public)
            self.assertNotIn('created_at', public)

    def test_browser_can_preflight_profile_patch(self):
        from petsense.app import create_app
        client = create_app(start_background=False).test_client()
        response = client.options('/api/app/pets/1', headers={
            'Origin': 'http://localhost:8083', 'Access-Control-Request-Method': 'PATCH',
            'Access-Control-Request-Headers': 'content-type,x-petsense-session'})
        self.assertIn('PATCH', response.headers['Access-Control-Allow-Methods'])


if __name__ == '__main__':
    unittest.main()
