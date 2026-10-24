import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect

from petsense import db

EXPECTED_TABLES = {'users', 'auth_sessions', 'pets', 'daily_stats', 'home_layout', 'posts',
                   'post_likes', 'comments', 'follows', 'readings', 'action_predictions',
                   'collection_sessions', 'collection_frames', 'collection_videos', 'journal_crc', 'devices'}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        db.configure('sqlite:///' + holder.name + '/petsense.db')

    def test_upgrade_creates_all_tables(self):
        db.init_db()
        self.assertTrue(EXPECTED_TABLES <= set(inspect(db.engine()).get_table_names()))

    def test_downgrade_base_is_clean(self):
        from alembic import command
        db.init_db()
        command.downgrade(db._alembic_config(), 'base')
        tables = set(inspect(db.engine()).get_table_names())
        # downgrade 到 base 后业务表全清（alembic_version 残留属正常）
        self.assertEqual(tables - {'alembic_version'}, set())

    def test_init_db_is_idempotent(self):
        db.init_db()
        db.init_db()
        self.assertIn('users', inspect(db.engine()).get_table_names())


if __name__ == '__main__':
    unittest.main()
