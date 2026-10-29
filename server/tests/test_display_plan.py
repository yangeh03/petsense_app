import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from PIL import Image
from helpers import fresh_db
from test_devices import seed_user, make_session_token
from petsense import db
from petsense.db import iso_z
from petsense.db.models import Device
from petsense.uwb.routes import bp


class DisplayPlanTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)
        self.addCleanup(lambda: (db.Session.remove(), db.engine().dispose()))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, PETSENSE_UWB_PLAN_ROOT=str(self.root)).start()
        self.owner = seed_user('plan-owner')
        self.other = seed_user('plan-other')
        self.headers = {'X-Petsense-Session': make_session_token(self.owner)}
        with db.session_scope() as s:
            claimed = db.utc_now()
            s.add(Device(device_id='collar-plan', owner_id=self.owner, name='', claimed_at=claimed))
        self.entry = dict(directory='room', ownerId=self.owner, claimedAt=iso_z(claimed))
        self.write_binding()
        directory = self.root / 'room';directory.mkdir()
        Image.new('RGB', (13, 9), 'white').save(directory / 'floorplan.png')
        self.meta = dict(schemaVersion=1, planId='room', name='场地', units='relative', calibrationStatus='required',
                         image=dict(file='floorplan.png', width=13, height=9))
        self.write_meta()
        app = Flask(__name__);app.register_blueprint(bp);self.client = app.test_client()
        self.url = '/api/app/uwb/plan?deviceId=collar-plan'

    def write_binding(self):
        (self.root / 'bindings.json').write_text(json.dumps({'devices': {'collar-plan': self.entry}}))

    def write_meta(self):
        (self.root / 'room/plan.json').write_text(json.dumps(self.meta))

    def test_only_owner_receives_relative_display_plan(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        other = {'X-Petsense-Session': make_session_token(self.other)}
        self.assertEqual(self.client.get(self.url, headers=other).status_code, 403)
        result = self.client.get(self.url, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['plan']['calibrationStatus'], 'required')
        self.assertTrue(result.json['plan']['image']['imageDataUri'].startswith('data:image/png;base64,'))
        self.assertNotIn('position', result.json['plan'])
        self.assertEqual(result.headers['Cache-Control'], 'no-store')

    def test_old_claim_does_not_reuse_plan(self):
        self.entry['claimedAt'] = '2000-01-01T00:00:00Z';self.write_binding()
        self.assertIsNone(self.client.get(self.url, headers=self.headers).json['plan'])

    def test_no_binding_is_compatible(self):
        (self.root / 'bindings.json').unlink()
        self.assertIsNone(self.client.get(self.url, headers=self.headers).json['plan'])

    def test_invalid_asset_and_traversal_are_rejected(self):
        self.meta['image']['width'] = 14;self.write_meta()
        self.assertEqual(self.client.get(self.url, headers=self.headers).status_code, 503)
        self.entry['directory'] = '../outside';self.write_binding()
        self.assertEqual(self.client.get(self.url, headers=self.headers).status_code, 503)

    def test_revision_changes_with_new_image(self):
        before = self.client.get(self.url, headers=self.headers).json['plan']['revision']
        Image.new('RGB', (13, 9), 'gray').save(self.root / 'room/floorplan.png')
        after = self.client.get(self.url, headers=self.headers).json['plan']['revision']
        self.assertNotEqual(before, after)
