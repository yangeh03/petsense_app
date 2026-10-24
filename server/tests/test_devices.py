import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect

from petsense import db
from petsense.db.models import Device
from helpers import fresh_db


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
