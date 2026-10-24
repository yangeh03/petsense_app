import sys
import unittest
import hashlib
import secrets
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import AuthSession, Pet, User
from petsense.social import routes as social_routes
from helpers import fresh_db


class SocialTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(social_routes.bp)
        self.client = app.test_client()
        self.tokens = {}
        with db.session_scope() as s:
            for name in ('alice', 'bob'):
                user = User(username=name, display_name=name, password_hash='x', salt='s',
                            avatar_emoji='🙂', bio='', created_at=db.utc_now())
                s.add(user)
                s.flush()
                s.add(Pet(user_id=user.id, name='Pet' + name, species='dog', breed='',
                          avatar_emoji='🐾', device_id='', created_at=db.utc_now()))
                token = secrets.token_urlsafe(16)
                from datetime import timedelta
                s.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                  user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
                self.tokens[name] = (user.id, token)

    def header(self, name):
        return {'X-Petsense-Session': self.tokens[name][1]}

    def test_post_like_comment_follow_wire_format(self):
        reply = self.client.post('/api/app/posts', headers=self.header('alice'), data={
            'caption': '你好 #标签', 'location': '滨江公园',
            'media_url': 'https://qioyo.site/petsenseIMG/storage/demo.gif',
            'media_kind': 'gif'})
        self.assertEqual(reply.status_code, 201)
        post_id = reply.get_json()['postId']
        feed_item = self.client.get('/api/app/feed').get_json()['posts'][0]
        self.assertEqual(feed_item['mediaKind'], 'gif')

        liked = self.client.post(f'/api/app/posts/{post_id}/like',
                                 headers=self.header('bob')).get_json()
        self.assertTrue(liked['liked'])
        self.assertEqual(liked['likes'], 1)

        self.assertEqual(self.client.post(f'/api/app/posts/{post_id}/comments',
                         headers=self.header('bob'), json={'body': '可爱'}).status_code, 201)
        comments = self.client.get(f'/api/app/posts/{post_id}/comments').get_json()['comments']
        self.assertEqual(comments[0]['body'], '可爱')
        self.assertEqual(comments[0]['user_id'], self.tokens['bob'][0])

        feed = self.client.get('/api/app/feed').get_json()['posts']
        item = feed[0]
        self.assertEqual(item['caption'], '你好 #标签')
        self.assertEqual(item['comments_count'], 1)
        self.assertEqual(item['likes'], 1)
        self.assertFalse(item['isMine'])
        self.assertEqual(item['imageUrl'], '')
        self.assertRegex(item['created_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')

        self.client.post(f"/api/app/users/{self.tokens['alice'][0]}/follow",
                         headers=self.header('bob'))
        profile = self.client.get(f"/api/app/users/{self.tokens['alice'][0]}").get_json()['profile']
        self.assertEqual(profile['followers'], 1)
        self.assertEqual(profile['following'], 0)
        self.assertEqual(profile['displayName'], 'alice')
        self.assertEqual(len(profile['pets']), 1)
        followers = self.client.get(
            f"/api/app/users/{self.tokens['alice'][0]}/followers").get_json()['users']
        self.assertEqual(followers[0]['id'], self.tokens['bob'][0])

        self.assertEqual(self.client.delete(f'/api/app/posts/{post_id}',
                         headers=self.header('bob')).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/app/posts/{post_id}',
                         headers=self.header('alice')).status_code, 200)
        self.assertEqual(self.client.get('/api/app/feed').get_json()['posts'], [])

    def test_comment_requires_post_and_follow_rules(self):
        self.assertEqual(self.client.post('/api/app/posts/999/comments',
                         headers=self.header('alice'), json={'body': 'x'}).status_code, 404)
        self.assertEqual(self.client.post(f"/api/app/users/{self.tokens['alice'][0]}/follow",
                         headers=self.header('alice')).status_code, 400)
        self.assertEqual(self.client.post('/api/app/users/999/follow',
                         headers=self.header('alice')).status_code, 404)


if __name__ == '__main__':
    unittest.main()
