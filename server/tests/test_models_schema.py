import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petsense.db.base import Base
import petsense.db.models  # noqa: F401  触发注册

EXPECTED_TABLES = {'users', 'auth_sessions', 'pets', 'daily_stats', 'home_layout', 'posts',
                   'post_likes', 'comments', 'follows', 'readings', 'action_predictions',
                   'collection_sessions', 'collection_frames', 'collection_videos', 'journal_crc'}


class ModelSchemaTests(unittest.TestCase):
    def test_all_tables_registered(self):
        self.assertEqual(set(Base.metadata.tables), EXPECTED_TABLES)

    def test_key_columns(self):
        readings = Base.metadata.tables['readings']
        self.assertEqual([column.name for column in readings.primary_key], ['id'])
        frames = Base.metadata.tables['collection_frames']
        self.assertEqual(sorted(column.name for column in frames.primary_key), ['boot', 'device', 'seq'])
        users = Base.metadata.tables['users']
        self.assertIn('created_at', users.c)

    def test_expected_indexes(self):
        index_names = {index.name for table in Base.metadata.tables.values()
                       for index in table.indexes}
        for expected in ('uq_users_username_lower', 'ix_posts_user_id_id',
                         'ix_readings_topic_received', 'action_predictions_device_time',
                         'collection_frames_session'):
            self.assertIn(expected, index_names)


if __name__ == '__main__':
    unittest.main()
