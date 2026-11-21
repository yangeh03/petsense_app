import io
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask
from PIL import Image

from helpers import fresh_db
from petsense import db
from petsense.auth.routes import issue_app_session
from petsense.db.models import Pet, TelemetryMinute, User
from petsense.petgif import daily_diary, diary_jobs, proxy
from petsense.telemetry.observations import SHANGHAI


class DailyDiaryTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(daily_diary.bp)
        self.client = app.test_client()
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(daily_diary.config, 'RUNTIME_ROOT', self.root))
        self.enterContext(patch.object(daily_diary.config, 'UPLOADS_DIR', self.root))
        self.enterContext(patch.object(daily_diary.config, 'PETGIF_TEMP_DIR', self.root))
        self.enterContext(patch.object(proxy, 'PETGIF_KEY', 'test-key'))
        self.upstream_pet = 'c601c1c3-92cc-410e-99ec-2d278f5e05af'
        self.upstream_diary = '1d638956-74e2-4650-af05-680cbbacd993'
        self.enterContext(patch.dict('os.environ', {
            'PETSENSE_PETGIF_DIARY_PET_ID': self.upstream_pet,
        }))
        Image.new('RGB', (20, 20), 'red').save(Path(self.root) / 'profile.png')
        self.enqueue = self.enterContext(patch.object(diary_jobs, 'enqueue_diary_job'))
        with db.session_scope() as session:
            owner = User(username='diary-owner', display_name='Owner', password_hash='x', salt='x', created_at=db.utc_now())
            other = User(username='diary-other', display_name='Other', password_hash='x', salt='x', created_at=db.utc_now())
            session.add_all((owner, other))
            session.flush()
            pet = Pet(user_id=owner.id, name='冬瓜', species='dog', device_id='owner-collar', avatar_image='profile.png', created_at=db.utc_now())
            session.add(pet)
            session.flush()
            self.ids = owner.id, other.id, pet.id
        self.owner, self.other = [
            {'X-Petsense-Session': issue_app_session(user_id)} for user_id in self.ids[:2]
        ]
        self.path = f'/api/app/exercise-diaries/{self.ids[2]}'

    def test_generates_from_profile_without_petgif_binding_and_serves_private_png(self):
        now = db.utc_now().astimezone(SHANGHAI).replace(hour=10, minute=0, second=0, microsecond=0)
        with db.session_scope() as session:
            for day, walking in ((now, 38), (now - timedelta(minutes=1), 40),
                                 (now - timedelta(days=1), 51)):
                session.add(TelemetryMinute(owner_id=self.ids[0], device_id='owner-collar',
                                            minute=day, actions=json.dumps({'walking': walking})))

        def submit(url, *, headers, files, data, **kwargs):
            self.assertTrue(url.endswith(f'/pets/{self.upstream_pet}/diaries'))
            self.assertEqual(headers, {'x-api-key': 'test-key'})
            part_name, (filename, stream, content_type) = files[0]
            self.assertEqual((part_name, content_type), ('photos', 'image/jpeg'))
            self.assertTrue(filename.endswith('.jpg'))
            with Image.open(stream) as source:
                self.assertGreater(source.getpixel((0, 0))[0], 240)
            self.assertEqual(data['steps'], 195)
            self.assertIn('忽略档案里的默认角色', data['notes'])
            return MagicMock(status_code=202, **{'json.return_value': {'diary': {'id': self.upstream_diary}}})

        png = io.BytesIO()
        Image.new('RGB', (600, 800)).save(png, format='PNG')
        status = MagicMock(status_code=200, **{'json.return_value': {'diary': {'status': 'complete'}}})
        download = MagicMock(status_code=200)
        download.raw = io.BytesIO(png.getvalue())
        download.__enter__.return_value = download
        with patch.object(daily_diary, 'utc_now', return_value=now):
            first = self.client.post(self.path, json={}, headers=self.owner)
            second = self.client.post(self.path, json={}, headers=self.owner)
            self.assertEqual(first.status_code, 202, first.json)
            self.assertEqual(first.json['jobId'], second.json['jobId'])
            self.assertEqual(first.json['input']['steps'], 195)
            self.enqueue.assert_called_once_with(first.json['jobId'])
            job = diary_jobs._read_job(first.json['jobId'])
            with patch.object(diary_jobs.requests, 'post', side_effect=submit) as create, \
                 patch.object(diary_jobs.requests, 'get', side_effect=[status, download]) as get:
                diary_jobs.run_diary_job(job.id)
                self.assertEqual(create.call_count, 1)
                self.assertTrue(get.call_args.args[0].endswith(f'/{self.upstream_diary}/image'))
                self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertFalse(Path(json.loads(job.photo_paths_json)[0]).exists())
            saved = self.client.get(self.path, headers=self.owner).json['jobs'][0]
            self.assertEqual(saved['status'], 'completed')
            poster = '/api/app' + saved['posterUrl']
            self.assertEqual(self.client.get(poster, headers=self.other).status_code, 404)
            with self.client.get(poster, headers=self.owner) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Image.open(io.BytesIO(response.data)).size, (600, 844))
            regenerated = self.client.post(self.path, json={'replaceJobId': job.id}, headers=self.owner)
            duplicate = self.client.post(self.path, json={'replaceJobId': job.id}, headers=self.owner)
            self.assertNotEqual(job.id, regenerated.json['jobId'])
            self.assertEqual(regenerated.json['jobId'], duplicate.json['jobId'])

    def test_no_data_missing_photo_and_old_client_do_not_submit(self):
        self.assertEqual(self.client.post(self.path, json={}, headers=self.owner).status_code, 409)
        self.assertEqual(self.client.post(self.path, json={'mode': 'template'}, headers=self.owner).status_code, 409)
        self.assertEqual(self.client.get(self.path, headers=self.other).status_code, 404)
        with db.session_scope() as session:
            session.get(Pet, self.ids[2]).avatar_image = None
        self.assertEqual(self.client.post(self.path, json={}, headers=self.owner).status_code, 422)
        self.enqueue.assert_not_called()

    def test_existing_template_remains_viewable_without_upstream_configuration(self):
        fields = {'mode': 'template', 'date': '2026-11-20', 'name': '冬瓜',
                  'walkingSeconds': None, 'steps': None}
        job, _ = diary_jobs.create_job(self.ids[0], self.ids[2], '', 'daily:2026-11-20:template:first', fields, [])
        diary_jobs.run_diary_job(job.id)
        with patch.dict('os.environ', {'PETSENSE_PETGIF_DIARY_PET_ID': ''}):
            result = self.client.get(self.path, headers=self.owner).json
            self.assertFalse(result['aiAvailable'])
            saved = result['jobs'][0]
            with self.client.get('/api/app' + saved['posterUrl'], headers=self.owner) as response:
                self.assertEqual(response.status_code, 200)
