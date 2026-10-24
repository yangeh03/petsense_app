import sys
import unittest
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import inspect

from petsense import db
from petsense.db.models import AuthSession, Device, Pet, User
from petsense.devices import routes as devices_routes
from petsense.pets import routes as pets_routes
from helpers import fresh_db


def make_session_token(user_id):
    token = secrets.token_urlsafe(16)
    with db.session_scope() as s:
        s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          user_id=user_id, expires_at=db.utc_now() + timedelta(days=1)))
    return token


def seed_user(username):
    with db.session_scope() as s:
        user = User(username=username, display_name=username, password_hash='x', salt='s',
                    avatar_emoji='🙂', bio='', created_at=db.utc_now())
        s.add(user)
        s.flush()
        return user.id


class DeviceApiTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(devices_routes.bp)
        app.register_blueprint(pets_routes.bp)
        self.client = app.test_client()
        self.alice = seed_user('alice')
        self.bob = seed_user('bob')
        self.header_alice = {'X-Petsense-Session': make_session_token(self.alice)}
        self.header_bob = {'X-Petsense-Session': make_session_token(self.bob)}

    def test_claim_binds_pet_and_conflicts(self):
        self.client.post('/api/app/pets', headers=self.header_alice,
                         json={'name': 'Max', 'deviceId': 'esp32-001'})
        device = self.client.get('/api/app/device', headers=self.header_alice).get_json()['device']
        self.assertEqual(device['deviceId'], 'esp32-001')
        self.assertEqual(device['petName'], 'Max')
        # 幂等
        self.assertEqual(self.client.post('/api/app/device/claim', headers=self.header_alice,
                         json={'deviceId': 'esp32-001'}).status_code, 200)
        # 他人认领冲突
        reply = self.client.post('/api/app/device/claim', headers=self.header_bob,
                                 json={'deviceId': 'esp32-001'})
        self.assertEqual(reply.status_code, 409)

    def test_claim_without_pet_then_create_binds(self):
        self.assertEqual(self.client.post('/api/app/device/claim', headers=self.header_alice,
                         json={'deviceId': 'esp32-9'}).get_json()['device']['petId'], None)
        self.client.post('/api/app/pets', headers=self.header_alice,
                         json={'name': 'Nick', 'deviceId': 'esp32-9'})
        with db.session_scope() as s:
            pet = s.query(Pet).one()
            self.assertEqual(pet.device_id, 'esp32-9')

    def test_release_clears_binding_and_frees_device(self):
        self.client.post('/api/app/pets', headers=self.header_alice,
                         json={'name': 'Max', 'deviceId': 'esp32-001'})
        self.assertEqual(self.client.post('/api/app/device/release', headers=self.header_alice,
                         json={'deviceId': 'esp32-001'}).status_code, 200)
        self.assertIsNone(self.client.get('/api/app/device',
                         headers=self.header_alice).get_json()['device'])
        with db.session_scope() as s:
            self.assertEqual(s.query(Pet).one().device_id, '')
        # 释放后他人可认领
        self.assertEqual(self.client.post('/api/app/device/claim', headers=self.header_bob,
                         json={'deviceId': 'esp32-001'}).status_code, 200)

    def test_single_pet_per_user(self):
        self.assertEqual(self.client.post('/api/app/pets', headers=self.header_alice,
                         json={'name': 'Max'}).status_code, 201)
        self.assertEqual(self.client.post('/api/app/pets', headers=self.header_alice,
                         json={'name': 'Nick'}).status_code, 409)

    def test_invalid_device_id_and_auth(self):
        self.assertEqual(self.client.get('/api/app/device').status_code, 401)
        self.assertEqual(self.client.post('/api/app/device/claim', headers=self.header_alice,
                         json={'deviceId': 'bad id!'}).status_code, 400)


class DeviceSchemaTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)

    def test_devices_table_shape(self):
        inspector = inspect(db.engine())
        columns = {column['name'] for column in inspector.get_columns('devices')}
        self.assertEqual(columns, {'device_id', 'owner_id', 'name', 'claimed_at', 'last_seen_at'})
        indexes = {index['name'] for index in inspector.get_indexes('devices')}
        self.assertIn('ix_devices_owner_id', indexes)

    def test_readings_has_device_id(self):
        columns = {column['name'] for column in inspect(db.engine()).get_columns('readings')}
        self.assertIn('device_id', columns)

    def test_owner_unique_enforced(self):
        from datetime import timedelta
        from petsense.db.models import User
        from sqlalchemy.exc import IntegrityError
        with db.session_scope() as s:
            users = []
            for name in ('a', 'b'):
                user = User(username=name, display_name=name, password_hash='x', salt='s',
                            avatar_emoji='🙂', bio='', created_at=db.utc_now())
                s.add(user)
                users.append(user)
            s.flush()
            s.add(Device(device_id='esp32-x', owner_id=users[0].id, name='', claimed_at=db.utc_now()))
        with self.assertRaises(IntegrityError):
            with db.session_scope() as s:
                s.add(Device(device_id='esp32-y', owner_id=1, name='', claimed_at=db.utc_now()))
        with db.session_scope() as s:
            s.add(Device(device_id='esp32-z', owner_id=2, name='', claimed_at=db.utc_now()))


if __name__ == '__main__':
    unittest.main()
