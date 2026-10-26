import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from petsense import db

EXPECTED_TABLES = {'users', 'auth_sessions', 'pets', 'daily_stats', 'home_layout', 'posts',
                   'post_likes', 'comments', 'follows', 'readings', 'action_predictions',
                   'collection_sessions', 'collection_frames', 'collection_videos', 'journal_crc', 'devices',
                   'telemetry_latest', 'telemetry_minutes', 'telemetry_events', 'petgif_diary_jobs',
                   'petgif_identities'}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        db.configure('sqlite:///' + holder.name + '/petsense.db')

    def test_upgrade_creates_all_tables(self):
        db.init_db()
        self.assertTrue(EXPECTED_TABLES <= set(inspect(db.engine()).get_table_names()))
        self.assertTrue({'post_bookmarks', 'stories', 'story_views', 'discovery_locations',
                         'discovery_queries',
                         'social_notifications', 'social_outbox', 'post_media'} <=
                        set(inspect(db.engine()).get_table_names()))
        self.assertIn('root_id', {column['name'] for column in
                                  inspect(db.engine()).get_columns('comments')})
        self.assertIn('avatar_template_id', {column['name'] for column in
                                              inspect(db.engine()).get_columns('pets')})

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

    def test_upgrade_existing_community_rows(self):
        from alembic import command
        command.upgrade(db._alembic_config(), '0006')
        with db.engine().begin() as connection:
            connection.execute(text("INSERT INTO users(id, username, display_name, password_hash, salt, "
                                    "avatar_emoji, bio, created_at) VALUES "
                                    "(1, 'old', 'old', 'x', 's', '🙂', '', '2026-01-01T00:00:00Z')"))
            connection.execute(text("INSERT INTO posts(id, user_id, pet_id, image_path, caption, "
                                    "location_text, media_url, media_kind, created_at) VALUES "
                                    "(1, 1, NULL, '', 'legacy', '', NULL, NULL, '2026-01-01T00:00:00Z')"))
        command.upgrade(db._alembic_config(), 'head')
        with db.engine().connect() as connection:
            row = connection.execute(text('SELECT caption, audience, version FROM posts WHERE id=1')).one()
            self.assertEqual(tuple(row), ('legacy', 'public', 1))


if __name__ == '__main__':
    unittest.main()
