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

    def test_dual_agent_migration_keeps_legacy_turns(self):
        """0021 → 0022：旧回合原样保留并可投影；新表、消息级引用与运行关联列到位；可降级回去。"""
        from alembic import command
        command.upgrade(db._alembic_config(), '0021')
        with db.engine().begin() as connection:
            connection.execute(text("INSERT INTO users(id, username, display_name, password_hash, salt, "
                                    "avatar_emoji, bio, created_at) VALUES "
                                    "(1, 'old', 'old', 'x', 's', '🙂', '', '2026-01-01T00:00:00Z')"))
            connection.execute(text("INSERT INTO pets(id, user_id, name, species, breed, avatar_emoji, "
                                    "device_id, created_at) VALUES "
                                    "(1, 1, '阿黄', 'dog', '', '🐾', '', '2026-01-01T00:00:00Z')"))
            connection.execute(text("INSERT INTO agent_turns(id, pet_id, request_id, text, target, sources, "
                                    "speaker, reply, status, created_at) VALUES "
                                    "(1, 1, 'k1', '今天走了多久', 'assistant', '[]', 'assistant', "
                                    "'【照料助手】40 分钟', 'complete', '2026-01-01T00:00:00Z')"))
        command.upgrade(db._alembic_config(), 'head')
        inspector = inspect(db.engine())
        self.assertTrue({'agent_routes', 'agent_messages'} <= set(inspector.get_table_names()))
        turn_columns = {column['name'] for column in inspector.get_columns('agent_turns')}
        self.assertTrue({'reply_to_message', 'pending_at'} <= turn_columns)
        self.assertIn('message_id', {column['name'] for column in inspector.get_columns('agent_runs')})
        with db.engine().connect() as connection:
            row = connection.execute(text('SELECT reply, reply_to_message FROM agent_turns WHERE id=1')).one()
            self.assertEqual(tuple(row), ('【照料助手】40 分钟', None))
        self.assertEqual([constraint['column_names'] for constraint in
                          inspect(db.engine()).get_unique_constraints('agent_messages')], [['turn_id', 'role']])
        command.downgrade(db._alembic_config(), '0021')
        inspector = inspect(db.engine())
        self.assertFalse({'agent_routes', 'agent_messages'} & set(inspector.get_table_names()))
        self.assertFalse({'reply_to_message', 'pending_at'}
                        & {column['name'] for column in inspector.get_columns('agent_turns')})

    def test_conversation_migration_backfills_legacy_turns(self):
        """0022 → 0023（W16）：每只有回合的宠物建一个「历史聊天」，旧回合与现有委托都指向它；可降级回去。"""
        from alembic import command
        command.upgrade(db._alembic_config(), '0022')
        with db.engine().begin() as connection:
            connection.execute(text("INSERT INTO users(id, username, display_name, password_hash, salt, "
                                    "avatar_emoji, bio, created_at) VALUES "
                                    "(1, 'old', 'old', 'x', 's', '🙂', '', '2026-01-01 00:00:00.000000')"))
            connection.execute(text("INSERT INTO pets(id, user_id, name, species, breed, avatar_emoji, "
                                    "device_id, created_at) VALUES "
                                    "(1, 1, '阿黄', 'dog', '', '🐾', '', '2026-01-01 00:00:00.000000'), "
                                    "(2, 1, '小白', 'cat', '', '🐾', '', '2026-01-01 00:00:00.000000')"))
            # 一条普通对话、一条迁移前的话题消息（topic_id 是话题根回合 id）与一条关怀消息（topic_id 是主题 id）
            connection.execute(text("INSERT INTO agent_turns(id, pet_id, request_id, text, target, topic_id, "
                                    "sources, speaker, reply, status, created_at) VALUES "
                                    "(1, 1, 'k1', '今天走了多久', 'assistant', NULL, '[]', 'assistant', "
                                    "'40 分钟', 'complete', '2026-01-01 00:00:00.000000'), "
                                    "(2, 1, 'k2', '那昨天呢', 'assistant', 1, '[]', 'assistant', "
                                    "'30 分钟', 'complete', '2026-01-02 00:00:00.000000'), "
                                    "(3, 1, 'care:event:9:open', '', 'assistant', 9, '[]', 'assistant', "
                                    "'电量偏低', 'complete', '2026-01-03 00:00:00.000000')"))
            connection.execute(text("INSERT INTO agent_tasks(id, pet_id, task_key, current_version, status, "
                                    "timezone, deadline_at, effective_from, paused_intervals, topic_id, "
                                    "due_attempts, created_at, updated_at) VALUES "
                                    "(1, 1, 't1', 1, 'observing', 'Asia/Shanghai', '2026-12-01 00:00:00.000000', "
                                    "'2026-01-01 00:00:00.000000', '[]', 1, 0, "
                                    "'2026-01-01 00:00:00.000000', '2026-01-01 00:00:00.000000')"))
        command.upgrade(db._alembic_config(), 'head')
        inspector = inspect(db.engine())
        self.assertIn('agent_conversations', inspector.get_table_names())
        for table in ('agent_turns', 'agent_care_topics', 'agent_tasks'):
            self.assertIn('conversation_id', {column['name'] for column in inspector.get_columns(table)})
        self.assertIn('uq_agent_care_topics_conversation_id',
                      {index['name'] for index in inspector.get_indexes('agent_care_topics')})
        with db.engine().connect() as connection:
            # 宠物 2 没有回合，不建会话；宠物 1 的三条回合（含两种旧话题消息）都归同一个「历史聊天」
            rows = connection.execute(text('SELECT id, pet_id, kind, title FROM agent_conversations')).fetchall()
            self.assertEqual([tuple(row) for row in rows], [(1, 1, 'chat', '历史聊天')])
            turns = connection.execute(text(
                'SELECT id, conversation_id, topic_id FROM agent_turns ORDER BY id')).fetchall()
            self.assertEqual([tuple(row) for row in turns], [(1, 1, None), (2, 1, 1), (3, 1, 9)])
            self.assertEqual(connection.execute(text(
                'SELECT conversation_id FROM agent_tasks WHERE id = 1')).scalar(), 1)
            self.assertEqual(connection.execute(text(
                'SELECT last_turn_at FROM agent_conversations WHERE id = 1')).scalar(),
                '2026-01-03 00:00:00.000000')
        command.downgrade(db._alembic_config(), '0022')
        inspector = inspect(db.engine())
        self.assertNotIn('agent_conversations', inspector.get_table_names())
        for table in ('agent_turns', 'agent_care_topics', 'agent_tasks'):
            self.assertNotIn('conversation_id', {column['name'] for column in inspector.get_columns(table)})
        with db.engine().connect() as connection:
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM agent_turns')).scalar(), 3)


if __name__ == '__main__':
    unittest.main()
