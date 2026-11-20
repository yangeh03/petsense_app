import io
import json
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from flask import Flask
from PIL import Image

from helpers import fresh_db
from petsense import db
from petsense.auth.routes import issue_app_session
from petsense.db.models import Pet, TelemetryMinute, User
from petsense.petgif import daily_diary, diary_jobs
from petsense.telemetry.observations import SHANGHAI


class DailyDiaryTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        app = Flask(__name__)
        app.register_blueprint(daily_diary.bp)
        self.client = app.test_client()
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(daily_diary.config, 'RUNTIME_ROOT', self.root))
        self.enqueue = self.enterContext(patch.object(diary_jobs, 'enqueue_diary_job'))
        with db.session_scope() as session:
            owner = User(username='diary-owner', display_name='Owner', password_hash='x', salt='x', created_at=db.utc_now())
            other = User(username='diary-other', display_name='Other', password_hash='x', salt='x', created_at=db.utc_now())
            session.add_all((owner, other))
            session.flush()
            pet = Pet(user_id=owner.id, name='冬瓜', species='dog', device_id='owner-collar', created_at=db.utc_now())
            session.add(pet)
            session.flush()
            self.ids = owner.id, other.id, pet.id
        self.owner, self.other = [
            {'X-Petsense-Session': issue_app_session(user_id)} for user_id in self.ids[:2]
        ]
        self.path = f'/api/app/exercise-diaries/{self.ids[2]}'

    def test_template_estimates_steps_reuses_job_and_serves_private_png(self):
        now = db.utc_now().astimezone(SHANGHAI).replace(hour=10, minute=0, second=0, microsecond=0)
        with db.session_scope() as session:
            for day, walking in ((now, 36), (now - timedelta(days=1), 51)):
                session.add(TelemetryMinute(owner_id=self.ids[0], device_id='owner-collar',
                                            minute=day, actions=json.dumps({'walking': walking})))
        with patch.object(daily_diary, 'utc_now', return_value=now):
            first = self.client.post(self.path, json={}, headers=self.owner)
            second = self.client.post(self.path, json={}, headers=self.owner)
            self.assertEqual(first.status_code, 202)
            self.assertEqual(first.json['jobId'], second.json['jobId'])
            self.assertEqual(first.json['input']['steps'], 90)
            self.enqueue.assert_called_once_with(first.json['jobId'])
            diary_jobs.run_diary_job(first.json['jobId'])
            collection = self.client.get(self.path, headers=self.owner).json
            job = collection['jobs'][0]
            self.assertEqual(job['status'], 'completed')
            poster = '/api/app' + job['posterUrl']
            self.assertEqual(self.client.get(poster, headers=self.other).status_code, 404)
            with self.client.get(poster, headers=self.owner) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Image.open(io.BytesIO(response.data)).size, (960, 1280))
            regenerated = self.client.post(self.path, json={'replaceJobId': job['jobId']}, headers=self.owner)
            duplicate = self.client.post(self.path, json={'replaceJobId': job['jobId']}, headers=self.owner)
            self.assertNotEqual(job['jobId'], regenerated.json['jobId'])
            self.assertEqual(regenerated.json['jobId'], duplicate.json['jobId'])

    def test_no_data_keeps_steps_missing_and_ai_is_unavailable(self):
        job = self.client.post(self.path, json={}, headers=self.owner)
        self.assertIsNone(job.json['input']['steps'])
        self.assertEqual(self.client.post(self.path, json={'mode': 'ai'}, headers=self.owner).status_code, 409)
        self.assertEqual(self.client.get(self.path, headers=self.other).status_code, 404)
