import sys
import unittest
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import select

from petsense import db
from petsense.auth import routes as auth_routes
from petsense.db.models import AuthSession, User
from helpers import fresh_db


class AuthTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(auth_routes.bp)
        self.client = app.test_client()

    def test_register_login_me_logout_flow(self):
        reply = self.client.post('/api/app/auth/register', json={
            'username': 'petsense', 'display_name': 'Max 爸爸', 'password': '123456', 'avatarEmoji': '🧑‍🌾'})
        self.assertEqual(reply.status_code, 200)
        token = reply.get_json()['token']
        headers = {'X-Petsense-Session': token}
        self.assertEqual(self.client.get('/api/app/auth/me', headers=headers)
                         .get_json()['user']['displayName'], 'Max 爸爸')
        reply = self.client.post('/api/app/auth/login', json={'username': 'PETSENSE', 'password': '123456'})
        self.assertEqual(reply.status_code, 200)
        self.client.post('/api/app/auth/logout', headers=headers)
        self.assertIsNone(self.client.get('/api/app/auth/me', headers=headers).get_json()['user'])

    def test_duplicate_register_409(self):
        body = {'username': 'a_user', 'display_name': 'a', 'password': '123456'}
        self.assertEqual(self.client.post('/api/app/auth/register', json=body).status_code, 200)
        self.assertEqual(self.client.post('/api/app/auth/register', json=body).status_code, 409)

    def test_wrong_password_and_expired_session(self):
        self.client.post('/api/app/auth/register',
                         json={'username': 'a_user', 'display_name': 'a', 'password': '123456'})
        self.assertEqual(self.client.post('/api/app/auth/login',
                         json={'username': 'a_user', 'password': 'wrong!'}).status_code, 401)
        expired_token = 'expired-token'
        with db.session_scope() as s:
            user_id = s.execute(select(User.id)).scalar_one()
            s.add(AuthSession(token_hash=hashlib.sha256(expired_token.encode()).hexdigest(),
                              user_id=user_id, expires_at=db.utc_now() - timedelta(days=1)))
        reply = self.client.get('/api/app/auth/me', headers={'X-Petsense-Session': expired_token})
        self.assertIsNone(reply.get_json()['user'])


if __name__ == '__main__':
    unittest.main()
