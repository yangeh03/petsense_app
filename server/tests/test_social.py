import sys
import unittest
import hashlib
import secrets
import io
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense import config
from petsense.db.models import AuthSession, Pet, User
from petsense.social import routes as social_routes
from helpers import fresh_db


class SocialTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        upload_dir = tempfile.TemporaryDirectory()
        old_upload_dir = config.UPLOADS_DIR
        old_social_dir = config.SOCIAL_MEDIA_DIR
        config.UPLOADS_DIR = upload_dir.name
        config.SOCIAL_MEDIA_DIR = upload_dir.name
        self.addCleanup(upload_dir.cleanup)
        self.addCleanup(setattr, config, 'UPLOADS_DIR', old_upload_dir)
        self.addCleanup(setattr, config, 'SOCIAL_MEDIA_DIR', old_social_dir)
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

    def test_missing_like_and_comments_are_not_successful(self):
        self.assertEqual(self.client.post('/api/app/posts/999/like',
                         headers=self.header('alice')).status_code, 404)
        self.assertEqual(self.client.get('/api/app/posts/999/comments').status_code, 404)

    def test_public_pet_does_not_expose_device_state(self):
        user_id = self.tokens['alice'][0]
        profile = self.client.get(f'/api/app/users/{user_id}').get_json()['profile']
        public_pet = profile['pets'][0]
        self.assertNotIn('device_id', public_pet)
        self.assertNotIn('batteryPercent', public_pet)
        self.assertNotIn('online', public_pet)

    def test_feed_cursor_and_detail_are_stable(self):
        alice = self.tokens['alice'][0]
        with db.session_scope() as s:
            from petsense.db.models import Post
            for index in range(65):
                s.add(Post(user_id=alice, image_path='', caption=f'post {index}',
                           location_text='', created_at=db.utc_now()))
        first = self.client.get('/api/app/feed?limit=20').get_json()
        self.assertEqual(len(first['posts']), 20)
        self.assertTrue(first['page']['hasMore'])
        cursor = first['page']['nextCursor']
        second = self.client.get(f'/api/app/feed?limit=20&cursor={cursor}').get_json()
        self.assertEqual(len(second['posts']), 20)
        self.assertFalse(set(p['id'] for p in first['posts']) &
                         set(p['id'] for p in second['posts']))
        post_id = first['posts'][0]['id']
        detail = self.client.get(f'/api/app/posts/{post_id}').get_json()
        self.assertEqual(detail['post']['id'], post_id)
        self.assertEqual(self.client.get('/api/app/feed?cursor=bogus').status_code, 400)

    def test_comment_reply_delete_and_idempotent_create(self):
        created = self.client.post('/api/app/posts', headers=self.header('alice'),
                                   data={'caption': 'x', 'media_url':
                                         'https://qioyo.site/petsenseIMG/storage/demo.gif'})
        post_id = created.get_json()['postId']
        root = self.client.post(f'/api/app/posts/{post_id}/comments',
                                headers=self.header('bob'),
                                json={'body': 'first', 'clientMutationId': 'root-1'})
        self.assertEqual(root.status_code, 201)
        root_id = root.get_json()['comment']['id']
        repeated = self.client.post(f'/api/app/posts/{post_id}/comments',
                                    headers=self.header('bob'),
                                    json={'body': 'first', 'clientMutationId': 'root-1'})
        self.assertEqual(repeated.get_json()['comment']['id'], root_id)
        self.assertEqual(self.client.post(f'/api/app/posts/{post_id}/comments',
                         headers=self.header('bob'), json={
                             'body': 'changed', 'clientMutationId': 'root-1'}).status_code, 409)
        reply = self.client.post(f'/api/app/posts/{post_id}/comments',
                                 headers=self.header('alice'),
                                 json={'body': 'thanks', 'replyToCommentId': root_id})
        self.assertEqual(reply.status_code, 201)
        reply_id = reply.get_json()['comment']['id']
        replies = self.client.get(f'/api/app/comments/{root_id}/replies').get_json()['comments']
        self.assertEqual(replies[0]['id'], reply_id)
        self.assertEqual(self.client.delete(f'/api/app/comments/{root_id}',
                         headers=self.header('alice')).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/app/comments/{root_id}',
                         headers=self.header('bob')).status_code, 200)
        roots = self.client.get(f'/api/app/posts/{post_id}/comments').get_json()['comments']
        self.assertEqual(roots[0]['body'], '')
        self.assertTrue(roots[0]['deleted'])
        detail = self.client.get(f'/api/app/posts/{post_id}').get_json()['post']
        self.assertEqual(detail['comments_count'], 1)
        self.assertEqual(self.client.delete(f'/api/app/comments/{reply_id}',
                         headers=self.header('alice')).status_code, 200)
        self.assertEqual(self.client.get(f'/api/app/posts/{post_id}/comments')
                         .get_json()['comments'], [])
        self.assertEqual(self.client.get(f'/api/app/posts/{post_id}')
                         .get_json()['post']['comments_count'], 0)

    def test_text_only_and_invalid_image(self):
        text_post = self.client.post('/api/app/posts', headers=self.header('alice'),
                                     data={'caption': '今天出门散步'})
        self.assertEqual(text_post.status_code, 201)
        invalid = self.client.post('/api/app/posts', headers=self.header('alice'), data={
            'caption': 'bad', 'image': (io.BytesIO(b'\x89PNG' + b'broken'), 'bad.png')})
        self.assertEqual(invalid.status_code, 400)
        missing_pet = self.client.post('/api/app/posts', headers=self.header('alice'),
                                       data={'caption': '带宠物发布', 'pet_id': '999999'})
        self.assertEqual(missing_pet.status_code, 400)

    def test_followers_audience_is_enforced_across_reads_and_interactions(self):
        created = self.client.post('/api/app/posts', headers=self.header('alice'),
                                   data={'caption': 'private to followers', 'audience': 'followers'})
        post_id = created.get_json()['postId']
        self.assertEqual(self.client.get(f'/api/app/posts/{post_id}').status_code, 404)
        self.assertFalse(any(p['id'] == post_id for p in
                             self.client.get('/api/app/feed').get_json()['posts']))
        self.assertEqual(self.client.post(f'/api/app/posts/{post_id}/like',
                         headers=self.header('bob')).status_code, 404)
        self.client.post(f"/api/app/users/{self.tokens['alice'][0]}/follow",
                         headers=self.header('bob'))
        self.assertEqual(self.client.get(f'/api/app/posts/{post_id}',
                         headers=self.header('bob')).status_code, 200)
        self.assertEqual(self.client.get(f'/api/app/posts/{post_id}',
                         headers=self.header('alice')).status_code, 200)

    def test_target_state_reactions_are_repeatable(self):
        created = self.client.post('/api/app/posts', headers=self.header('alice'),
                                   data={'caption': 'x'})
        post_id = created.get_json()['postId']
        path = f'/api/app/posts/{post_id}/like'
        for _ in range(2):
            response = self.client.put(path, headers=self.header('bob')).get_json()
            self.assertTrue(response['liked'])
            self.assertEqual(response['likes'], 1)
        self.assertEqual(self.client.delete(path, headers=self.header('bob')).get_json()['likes'], 0)
        bookmark = f'/api/app/posts/{post_id}/bookmark'
        self.assertTrue(self.client.put(bookmark, headers=self.header('bob')).get_json()['bookmarked'])
        self.assertTrue(self.client.put(bookmark, headers=self.header('bob')).get_json()['bookmarked'])
        self.assertFalse(self.client.delete(bookmark, headers=self.header('bob')).get_json()['bookmarked'])
        target = self.tokens['alice'][0]
        follow = f'/api/app/users/{target}/follow'
        self.assertTrue(self.client.put(follow, headers=self.header('bob')).get_json()['following'])
        self.assertTrue(self.client.put(follow, headers=self.header('bob')).get_json()['following'])
        self.assertFalse(self.client.delete(follow, headers=self.header('bob')).get_json()['following'])

    def test_server_search_reaches_old_posts_users_and_places(self):
        alice = self.tokens['alice'][0]
        with db.session_scope() as s:
            from petsense.db.models import Post
            s.add(Post(user_id=alice, image_path='', caption='柯基海边散步',
                       location_text='滨江公园', created_at=db.utc_now()))
            s.add(Post(user_id=alice, image_path='', caption='滨江别处',
                       location_text='另一公园', created_at=db.utc_now()))
            for index in range(65):
                s.add(Post(user_id=alice, image_path='', caption=f'新帖{index}',
                           location_text='', created_at=db.utc_now()))
        posts = self.client.get('/api/app/search?q=柯基&type=posts').get_json()['posts']
        self.assertEqual(len(posts), 1)
        users = self.client.get('/api/app/search?q=alice&type=users').get_json()['users']
        self.assertEqual(users[0]['id'], alice)
        places = self.client.get('/api/app/search?q=滨江&type=places').get_json()['places']
        self.assertEqual(places[0]['name'], '滨江公园')
        exact = self.client.get('/api/app/search?q=滨江&type=posts&place=滨江公园')
        self.assertEqual(exact.status_code, 200)
        self.assertTrue(exact.get_json()['posts'])
        self.assertTrue(all(post['location_text'] == '滨江公园'
                            for post in exact.get_json()['posts']))

    def test_story_audience_view_and_delete(self):
        from PIL import Image
        image_bytes = io.BytesIO()
        Image.new('RGB', (3, 3), 'orange').save(image_bytes, format='PNG')
        image_bytes.seek(0)
        created = self.client.post('/api/app/stories', headers=self.header('alice'),
                                   data={'image': (image_bytes, 'story.png'),
                                         'caption': 'hello', 'audience': 'followers'})
        self.assertEqual(created.status_code, 201)
        story_id = created.get_json()['story']['id']
        self.assertEqual(self.client.get(f'/api/app/stories/{story_id}').status_code, 404)
        self.client.put(f"/api/app/users/{self.tokens['alice'][0]}/follow",
                        headers=self.header('bob'))
        self.assertEqual(self.client.get(f'/api/app/stories/{story_id}',
                         headers=self.header('bob')).status_code, 200)
        self.assertEqual(self.client.get(f'/api/app/stories/{story_id}/media').status_code, 404)
        self.assertEqual(self.client.get(f'/api/app/stories/{story_id}/media',
                         headers=self.header('bob')).status_code, 200)
        self.assertEqual(self.client.put(f'/api/app/stories/{story_id}/view',
                         headers=self.header('bob')).status_code, 200)
        self.assertEqual(self.client.put(f'/api/app/stories/{story_id}/view',
                         headers=self.header('bob')).status_code, 200)
        viewers = self.client.get(f'/api/app/stories/{story_id}/viewers',
                                  headers=self.header('alice')).get_json()['viewers']
        self.assertEqual(len(viewers), 1)
        self.assertEqual(self.client.delete(f'/api/app/stories/{story_id}',
                         headers=self.header('alice')).status_code, 200)
        self.assertEqual(self.client.get(f'/api/app/stories/{story_id}',
                         headers=self.header('bob')).status_code, 404)

    def test_expired_story_media_and_discovery_query_are_pruned(self):
        from datetime import timedelta
        from petsense.db.models import Story, DiscoveryQuery
        from petsense.social.jobs import prune_expired_social_data
        image_bytes = io.BytesIO()
        from PIL import Image
        Image.new('RGB', (8, 8)).save(image_bytes, 'PNG')
        image_bytes.seek(0)
        created = self.client.post('/api/app/stories', headers=self.header('alice'),
                                   data={'image': (image_bytes, 'story.png')})
        story_id = created.get_json()['story']['id']
        with db.session_scope() as s:
            story = s.get(Story, story_id)
            path = Path(config.SOCIAL_MEDIA_DIR) / story.image_path
            story.expires_at = db.utc_now() - timedelta(seconds=1)
            s.add(DiscoveryQuery(id='expired-query', user_id=self.tokens['alice'][0],
                  center_latitude_grid=0, center_longitude_grid=0, radius_km=5,
                  candidate_ids_json='[]', created_at=db.utc_now() - timedelta(minutes=20),
                  expires_at=db.utc_now() - timedelta(seconds=1)))
        self.assertTrue(path.exists())
        self.assertEqual(self.client.get(f'/api/app/stories/{story_id}').status_code, 404)
        prune_expired_social_data()
        self.assertFalse(path.exists())
        with db.session_scope() as s:
            self.assertIsNone(s.get(Story, story_id))
            self.assertIsNone(s.get(DiscoveryQuery, 'expired-query'))

    def test_followers_post_media_requires_current_relationship(self):
        from PIL import Image
        image_bytes = io.BytesIO()
        Image.new('RGB', (3, 3), 'orange').save(image_bytes, format='PNG')
        image_bytes.seek(0)
        created = self.client.post('/api/app/posts', headers=self.header('alice'), data={
            'caption': '小秘密', 'audience': 'followers',
            'image': (image_bytes, 'photo.png')})
        self.assertEqual(created.status_code, 201)
        post_id = created.get_json()['postId']
        url = f'/api/app/posts/{post_id}/media/0'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.put(f"/api/app/users/{self.tokens['alice'][0]}/follow",
                        headers=self.header('bob'))
        self.assertEqual(self.client.get(url, headers=self.header('bob')).status_code, 200)
        self.client.delete(f"/api/app/users/{self.tokens['alice'][0]}/follow",
                           headers=self.header('bob'))
        self.assertEqual(self.client.get(url, headers=self.header('bob')).status_code, 404)

    def test_nearby_opt_in_coarse_location_and_live_opt_out(self):
        put = self.client.put('/api/app/me/discovery-location', headers=self.header('alice'),
                              json={'latitude': 39.900123, 'longitude': 116.400987,
                                    'accuracyMeters': 30, 'enabled': True})
        self.assertEqual(put.status_code, 200)
        from petsense.db.models import DiscoveryLocation
        with db.session_scope() as s:
            stored = s.get(DiscoveryLocation, self.tokens['alice'][0])
            self.assertNotEqual(stored.latitude_grid, 39.900123)
            self.assertNotEqual(stored.longitude_grid, 116.400987)
        query = self.client.post('/api/app/nearby/queries', headers=self.header('bob'),
                                 json={'latitude': 39.901, 'longitude': 116.401,
                                       'accuracyMeters': 30, 'radiusKm': 5})
        self.assertEqual(query.status_code, 201)
        query_id = query.get_json()['queryId']
        nearby = self.client.get(f'/api/app/nearby/users?queryId={query_id}',
                                 headers=self.header('bob')).get_json()['users']
        self.assertEqual(nearby[0]['id'], self.tokens['alice'][0])
        self.assertNotIn('latitude', nearby[0])
        self.assertNotIn('longitude', nearby[0])
        self.client.delete('/api/app/me/discovery-location', headers=self.header('alice'))
        self.assertEqual(self.client.get(f'/api/app/nearby/users?queryId={query_id}',
                         headers=self.header('bob')).get_json()['users'], [])

    def test_nearby_posts_respect_live_opt_out_and_audience(self):
        alice_id = self.tokens['alice'][0]
        post = self.client.post('/api/app/posts', headers=self.header('alice'),
                                data={'caption': '附近动态', 'audience': 'public'})
        post_id = post.get_json()['postId']
        self.client.put('/api/app/me/discovery-location', headers=self.header('alice'),
                        json={'latitude': 39.900123, 'longitude': 116.400987,
                              'accuracyMeters': 30, 'enabled': True})
        query = self.client.post('/api/app/nearby/queries', headers=self.header('bob'),
                                 json={'latitude': 39.901, 'longitude': 116.401,
                                       'accuracyMeters': 30, 'radiusKm': 5})
        query_id = query.get_json()['queryId']
        url = f'/api/app/nearby/posts?queryId={query_id}'
        result = self.client.get(url, headers=self.header('bob'))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['posts'][0]['id'], post_id)
        self.client.patch(f'/api/app/posts/{post_id}', headers=self.header('alice'),
                          json={'caption': '附近动态', 'audience': 'followers', 'version': 1})
        self.assertEqual(self.client.get(url, headers=self.header('bob')).get_json()['posts'], [])
        self.client.put(f'/api/app/users/{alice_id}/follow', headers=self.header('bob'))
        self.assertEqual(self.client.get(url, headers=self.header('bob'))
                         .get_json()['posts'][0]['id'], post_id)
        self.client.delete('/api/app/me/discovery-location', headers=self.header('alice'))
        self.assertEqual(self.client.get(url, headers=self.header('bob')).get_json()['posts'], [])

    def test_social_notifications_survive_worker_retry_and_read_watermark(self):
        from petsense.social.jobs import drain_social_outbox
        created = self.client.post('/api/app/posts', headers=self.header('alice'),
                                   data={'caption': '通知测试'})
        post_id = created.get_json()['postId']
        self.client.put(f'/api/app/posts/{post_id}/like', headers=self.header('bob'))
        drain_social_outbox()
        drain_social_outbox()
        inbox = self.client.get('/api/app/notifications',
                                headers=self.header('alice')).get_json()
        self.assertEqual(len(inbox['notifications']), 1)
        self.assertEqual(inbox['unreadCount'], 1)
        watermark = inbox['notifications'][0]['id']
        self.client.put('/api/app/notifications/read-through',
                        headers=self.header('alice'), json={'highWaterId': watermark})
        self.client.post(f'/api/app/posts/{post_id}/comments',
                         headers=self.header('bob'), json={'body': '新评论'})
        drain_social_outbox()
        inbox = self.client.get('/api/app/notifications',
                                headers=self.header('alice')).get_json()
        self.assertEqual(inbox['unreadCount'], 1)

    def test_author_can_edit_with_version_check(self):
        created = self.client.post('/api/app/posts', headers=self.header('alice'),
                                   data={'caption': 'before'})
        post_id = created.get_json()['postId']
        self.assertEqual(self.client.patch(f'/api/app/posts/{post_id}',
                         headers=self.header('bob'), json={'caption': 'bad', 'version': 1}).status_code, 403)
        changed = self.client.patch(f'/api/app/posts/{post_id}',
                                    headers=self.header('alice'),
                                    json={'caption': 'after', 'location': '滨江公园',
                                          'audience': 'followers', 'version': 1})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.get_json()['post']['version'], 2)
        self.assertEqual(changed.get_json()['post']['location_text'], '滨江公园')
        self.assertEqual(self.client.patch(f'/api/app/posts/{post_id}',
                         headers=self.header('alice'), json={'caption': 'stale', 'version': 1}).status_code, 409)

    def test_follow_list_paginates_without_duplicates(self):
        from petsense.db.models import Follow
        alice = self.tokens['alice'][0]
        bob = self.tokens['bob'][0]
        with db.session_scope() as s:
            guest = User(username='charlie', display_name='charlie', password_hash='x',
                         salt='s', avatar_emoji='🙂', bio='', created_at=db.utc_now())
            s.add(guest)
            s.flush()
            s.add(Follow(follower_id=bob, followee_id=alice, created_at=db.utc_now()))
            s.add(Follow(follower_id=guest.id, followee_id=alice, created_at=db.utc_now()))
        first = self.client.get(f'/api/app/users/{alice}/followers?limit=1').get_json()
        self.assertEqual(len(first['users']), 1)
        self.assertTrue(first['page']['hasMore'])
        second = self.client.get(f'/api/app/users/{alice}/followers?limit=1&cursor='
                                 f'{first["page"]["nextCursor"]}').get_json()
        self.assertNotEqual(first['users'][0]['id'], second['users'][0]['id'])

    def test_bookmarks_are_private_and_removed_with_deleted_post(self):
        created = self.client.post('/api/app/posts', headers=self.header('alice'),
                                   data={'caption': 'save me'})
        post_id = created.get_json()['postId']
        self.client.put(f'/api/app/posts/{post_id}/bookmark', headers=self.header('bob'))
        saved = self.client.get('/api/app/me/bookmarks',
                                headers=self.header('bob')).get_json()['posts']
        self.assertEqual(saved[0]['id'], post_id)
        self.assertEqual(self.client.get('/api/app/me/bookmarks').status_code, 401)
        self.client.delete(f'/api/app/posts/{post_id}', headers=self.header('alice'))
        self.assertEqual(self.client.get('/api/app/me/bookmarks',
                         headers=self.header('bob')).get_json()['posts'], [])

    def test_profile_posts_count_hides_followers_only_posts_from_strangers(self):
        alice = self.tokens['alice'][0]
        self.client.post('/api/app/posts', headers=self.header('alice'), data={'caption': 'public'})
        self.client.post('/api/app/posts', headers=self.header('alice'),
                         data={'caption': 'secret', 'audience': 'followers'})
        self.assertEqual(self.client.get(f'/api/app/users/{alice}').get_json()['profile']['posts_count'], 1)
        self.assertEqual(self.client.get(f'/api/app/users/{alice}', headers=self.header('bob'))
                         .get_json()['profile']['posts_count'], 1)
        self.assertEqual(self.client.get(f'/api/app/users/{alice}', headers=self.header('alice'))
                         .get_json()['profile']['posts_count'], 2)
        self.client.put(f'/api/app/users/{alice}/follow', headers=self.header('bob'))
        self.assertEqual(self.client.get(f'/api/app/users/{alice}', headers=self.header('bob'))
                         .get_json()['profile']['posts_count'], 2)

    def test_mutation_key_race_loser_gets_replay_not_500(self):
        first = self.client.post('/api/app/posts', headers=self.header('alice'),
                                 data={'caption': 'race', 'client_mutation_id': 'race-1'})
        self.assertEqual(first.status_code, 201)
        post_id = first.get_json()['postId']
        comment = self.client.post(f'/api/app/posts/{post_id}/comments', headers=self.header('bob'),
                                   json={'body': 'hi', 'clientMutationId': 'race-c'})
        self.assertEqual(comment.status_code, 201)
        comment_id = comment.get_json()['comment']['id']
        from PIL import Image
        image_bytes = io.BytesIO()
        Image.new('RGB', (3, 3), 'orange').save(image_bytes, format='PNG')
        story = self.client.post('/api/app/stories', headers=self.header('alice'), data={
            'caption': 's', 'client_mutation_id': 'race-s',
            'image': (io.BytesIO(image_bytes.getvalue()), 'a.png')})
        self.assertEqual(story.status_code, 201)
        story_id = story.get_json()['story']['id']

        # 模拟并发窗口：落败请求的幂等预检查没看到赢家行，插入时撞唯一约束。
        original = social_routes.find_mutation_row
        social_routes.find_mutation_row = lambda *args, **kwargs: None
        try:
            replay = self.client.post('/api/app/posts', headers=self.header('alice'),
                                      data={'caption': 'race', 'client_mutation_id': 'race-1'})
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.get_json()['postId'], post_id)
            conflict = self.client.post('/api/app/posts', headers=self.header('alice'),
                                        data={'caption': 'other', 'client_mutation_id': 'race-1'})
            self.assertEqual(conflict.status_code, 409)
            comment_replay = self.client.post(f'/api/app/posts/{post_id}/comments',
                                              headers=self.header('bob'),
                                              json={'body': 'hi', 'clientMutationId': 'race-c'})
            self.assertEqual(comment_replay.status_code, 200)
            self.assertEqual(comment_replay.get_json()['comment']['id'], comment_id)
            story_replay = self.client.post('/api/app/stories', headers=self.header('alice'), data={
                'caption': 's', 'client_mutation_id': 'race-s',
                'image': (io.BytesIO(image_bytes.getvalue()), 'b.png')})
            self.assertEqual(story_replay.status_code, 200)
            self.assertEqual(story_replay.get_json()['story']['id'], story_id)
        finally:
            social_routes.find_mutation_row = original
        self.assertEqual(len(list(Path(config.SOCIAL_MEDIA_DIR).glob('story_*'))), 1)

    def test_replaying_deleted_post_mutation_returns_gone(self):
        first = self.client.post('/api/app/posts', headers=self.header('alice'),
                                 data={'caption': 'gone', 'client_mutation_id': 'gone-1'})
        post_id = first.get_json()['postId']
        self.client.delete(f'/api/app/posts/{post_id}', headers=self.header('alice'))
        replay = self.client.post('/api/app/posts', headers=self.header('alice'),
                                  data={'caption': 'gone', 'client_mutation_id': 'gone-1'})
        self.assertEqual(replay.status_code, 410)
        self.assertEqual(self.client.get('/api/app/feed').get_json()['posts'], [])

    def test_comment_context_locates_root_and_respects_visibility(self):
        post_id = self.client.post('/api/app/posts', headers=self.header('alice'),
                                   data={'caption': 'ctx', 'audience': 'followers'}).get_json()['postId']
        root = self.client.post(f'/api/app/posts/{post_id}/comments', headers=self.header('alice'),
                                json={'body': 'root'}).get_json()['comment']
        reply = self.client.post(f'/api/app/posts/{post_id}/comments', headers=self.header('alice'),
                                 json={'body': 'reply', 'replyToCommentId': root['id']}).get_json()['comment']
        context = self.client.get(f"/api/app/comments/{reply['id']}/context",
                                  headers=self.header('alice')).get_json()
        self.assertEqual(context['context'], {'commentId': reply['id'], 'rootId': root['id'],
                                              'postId': post_id})
        root_context = self.client.get(f"/api/app/comments/{root['id']}/context",
                                       headers=self.header('alice')).get_json()['context']
        self.assertIsNone(root_context['rootId'])
        # bob 不是粉丝，看不到仅粉丝帖，也就不能通过评论上下文探测到它。
        self.assertEqual(self.client.get(f"/api/app/comments/{reply['id']}/context",
                         headers=self.header('bob')).status_code, 404)
        self.assertEqual(self.client.get('/api/app/comments/999/context').status_code, 404)

    def test_public_profile_edit_uses_version_and_safe_fields(self):
        profile = self.client.patch('/api/app/me/public-profile', headers=self.header('alice'),
                                    json={'displayName': '小橘', 'bio': '喜欢遛狗',
                                          'avatarEmoji': '🐕', 'version': 1})
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.get_json()['profile']['version'], 2)
        other = self.client.get(f"/api/app/users/{self.tokens['alice'][0]}").get_json()['profile']
        self.assertEqual(other['displayName'], '小橘')
        self.assertEqual(self.client.patch('/api/app/me/public-profile',
                         headers=self.header('alice'), json={'displayName': 'old',
                         'version': 1}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
