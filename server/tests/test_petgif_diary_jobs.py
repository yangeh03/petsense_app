import hashlib
import io
import json
import secrets
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask

from petsense import db
from petsense.db.models import AuthSession, Pet, PetgifDiaryJob, User
from petsense.petgif import diary_jobs, proxy
from helpers import fresh_db
from PIL import Image


class PetgifDiaryJobTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(proxy.bp)
        self.client = app.test_client()
        self.token = secrets.token_urlsafe(20)
        with db.session_scope() as session:
            user = User(username='diary-user', display_name='Diary User', password_hash='x', salt='s',
                        avatar_emoji='🐶', bio='', created_at=db.utc_now())
            session.add(user)
            session.flush()
            self.user_id = user.id
            pet = Pet(user_id=user.id, name='Momo', species='dog', breed='', avatar_emoji='🐶',
                      device_id='', petgif_id='personal-petgif', created_at=db.utc_now())
            session.add(pet)
            session.flush()
            self.pet_id = pet.id
            session.add(AuthSession(token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
                                    user_id=user.id, expires_at=db.utc_now() + timedelta(days=1)))
        self.headers = {'X-Petsense-Session': self.token}

    def submit(self, key='request-123', fields=None, files=None, headers=None):
        payload = {'durationMin': '25', 'steps': '1300', 'notes': '散步'}
        payload.update(fields or {})
        payload.update(files or {})
        return self.client.post(
            f'/api/app/petgif/pets/{self.pet_id}/diaries',
            headers={**self.headers, 'Idempotency-Key': key, **(headers or {})},
            data=payload,
            content_type='multipart/form-data',
        )

    def test_submit_returns_a_stable_queued_job_for_an_owned_personal_pet(self):
        with patch.object(diary_jobs, 'enqueue_diary_job') as enqueue:
            response = self.submit()
            repeated = self.submit()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json['status'], 'queued')
        self.assertTrue(response.json['jobId'])
        self.assertEqual(repeated.json['jobId'], response.json['jobId'])
        enqueue.assert_called_once_with(response.json['jobId'])

    def test_submit_rejects_missing_personal_identity_and_other_pet(self):
        with db.session_scope() as session:
            session.get(Pet, self.pet_id).petgif_id = None
        with patch.object(diary_jobs, 'enqueue_diary_job') as enqueue:
            self.assertEqual(self.submit().status_code, 409)
            self.assertEqual(self.client.post(
                '/api/app/petgif/pets/999/diaries', headers={
                    **self.headers, 'Idempotency-Key': 'other-pet',
                }, data={'durationMin': '25', 'steps': '0'},
            ).status_code, 404)
        enqueue.assert_not_called()

    def test_submit_rejects_shared_preset_as_a_personal_diary_identity(self):
        with db.session_scope() as session:
            session.get(Pet, self.pet_id).petgif_id = '0104965e-0ea3-4b79-92a6-975af775950b'
        with patch.object(diary_jobs, 'enqueue_diary_job') as enqueue:
            response = self.submit()
        self.assertEqual(response.status_code, 409)
        enqueue.assert_not_called()

    def test_submit_validates_numeric_bounds_photo_count_and_mime(self):
        with patch.object(diary_jobs, 'enqueue_diary_job'):
            for fields in ({'durationMin': '0'}, {'durationMin': '601'}, {'steps': '-1'},
                           {'steps': '100001'}, {'steps': '1.5'}, {'notes': '长' * 201}):
                with self.subTest(fields=fields):
                    self.assertEqual(self.submit(key='invalid-' + str(len(fields)), fields=fields)
                                     .status_code, 400)
            bad_photo = (io.BytesIO(b'plain text'), 'bad.txt', 'text/plain')
            self.assertEqual(self.submit(key='bad-photo', files={'photos': bad_photo}).status_code, 400)
            oversized = (io.BytesIO(b'x' * (diary_jobs.MAX_PHOTO_BYTES + 1)), 'large.png', 'image/png')
            self.assertEqual(self.submit(key='large-photo', files={'photos': oversized}).status_code, 400)
            too_many = {'photos': [
                self.photo_upload() for _ in range(4)
            ]}
            self.assertEqual(self.submit(key='too-many', files=too_many).status_code, 400)

    @staticmethod
    def photo_upload():
        data = io.BytesIO()
        Image.new('RGB', (8, 8), 'orange').save(data, format='PNG')
        data.seek(0)
        return (data, 'walk.png', 'image/png')

    def test_worker_posts_once_persists_remote_id_and_cleans_uploaded_photo(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(diary_jobs.config, 'PETGIF_TEMP_DIR', folder), \
                    patch.object(diary_jobs, 'enqueue_diary_job'):
                response = self.submit(files={'photos': self.photo_upload()})
            self.assertEqual(response.status_code, 202)
            job_id = response.json['jobId']
            with db.session_scope() as session:
                job = session.get(PetgifDiaryJob, job_id)
                photo_path = json.loads(job.photo_paths_json)[0]
            upstream_created = SimpleNamespace(
                status_code=201, ok=True, json=lambda: {'diary': {'id': 'upstream-1'}},
            )
            completed = SimpleNamespace(
                status_code=200, ok=True, raise_for_status=lambda: None,
                json=lambda: {'diary': {'status': 'complete', 'assets': {'image': {
                    'publicUrl': 'https://qioyo.site/petsenseIMG/storage/diary/poster.png',
                }}, 'stats': {'durationMin': 25, 'steps': 1300}}},
            )
            with patch.object(diary_jobs.requests, 'post', return_value=upstream_created) as post, \
                    patch.object(diary_jobs.requests, 'get', return_value=completed):
                diary_jobs.run_diary_job(job_id, poll_timeout=1)
            post.assert_called_once()
            self.assertIn('/pets/personal-petgif/diaries', post.call_args.args[0])
            self.assertFalse(Path(photo_path).exists())
            with db.session_scope() as session:
                job = session.get(PetgifDiaryJob, job_id)
                self.assertEqual(job.upstream_diary_id, 'upstream-1')
                self.assertEqual(job.status, 'completed')

    def test_recovery_marks_unidentified_submit_unknown_without_resubmitting(self):
        with patch.object(diary_jobs, 'enqueue_diary_job'):
            response = self.submit()
        with patch.object(diary_jobs, 'enqueue_diary_job') as enqueue:
            diary_jobs.recover_diary_jobs()
        enqueue.assert_not_called()
        status = self.client.get('/api/app/petgif/diary-jobs/' + response.json['jobId'],
                                 headers=self.headers)
        self.assertEqual(status.json['status'], 'unknown')
        with patch.object(diary_jobs, 'enqueue_diary_job') as enqueue:
            repeated = self.submit()
        self.assertEqual(repeated.json['jobId'], response.json['jobId'])
        self.assertEqual(repeated.json['status'], 'unknown')
        enqueue.assert_not_called()

    def test_job_status_and_history_are_private_to_the_authenticated_owner(self):
        with patch.object(diary_jobs, 'enqueue_diary_job'):
            response = self.submit()
        job_id = response.json['jobId']
        self.assertEqual(self.client.get('/api/app/petgif/diary-jobs/' + job_id).status_code, 401)
        from test_devices import seed_user, make_session_token
        stranger = {'X-Petsense-Session': make_session_token(seed_user('diary-stranger'))}
        self.assertEqual(self.client.get('/api/app/petgif/diary-jobs/' + job_id,
                                         headers=stranger).status_code, 404)
        self.assertEqual(self.client.get(f'/api/app/petgif/pets/{self.pet_id}/diary-jobs',
                                         headers=self.headers).json['jobs'][0]['jobId'], job_id)

    def test_diary_share_rejects_unowned_pet_before_downloading_poster(self):
        from test_devices import seed_user, make_session_token
        stranger = {'X-Petsense-Session': make_session_token(seed_user('diary-share-stranger'))}
        with patch.object(proxy.requests, 'get') as download:
            response = self.client.post('/api/app/petgif/diary-post', headers=stranger, json={
                'posterUrl': 'https://qioyo.site/petsenseIMG/storage/diary/poster.png',
                'petId': self.pet_id,
            })
        self.assertEqual(response.status_code, 404)
        download.assert_not_called()

    def test_legacy_shared_id_diary_endpoint_is_disabled(self):
        response = self.client.post('/api/app/petgif/pet/preset-id/diary')
        self.assertEqual(response.status_code, 410)


if __name__ == '__main__':
    unittest.main()
