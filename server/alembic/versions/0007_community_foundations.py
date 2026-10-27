"""Add public community lifecycle, interaction and discovery foundations."""

from alembic import op
import sqlalchemy as sa

from petsense.db.base import UTCDatetime

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('profile_version', sa.Integer(), nullable=False,
                                     server_default='1'))
    with op.batch_alter_table('posts') as batch:
        batch.add_column(sa.Column('updated_at', UTCDatetime(), nullable=True))
        batch.add_column(sa.Column('deleted_at', UTCDatetime(), nullable=True))
        batch.add_column(sa.Column('audience', sa.String(16), nullable=False,
                                   server_default='public'))
        batch.add_column(sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
        batch.add_column(sa.Column('client_mutation_id', sa.String(80), nullable=True))
    op.create_index('uq_posts_mutation', 'posts', ['user_id', 'client_mutation_id'], unique=True)
    with op.batch_alter_table('comments') as batch:
        batch.add_column(sa.Column('root_id', sa.Integer(), sa.ForeignKey('comments.id', name='fk_comments_root'), nullable=True))
        batch.add_column(sa.Column('reply_to_comment_id', sa.Integer(),
                                   sa.ForeignKey('comments.id', name='fk_comments_reply_to'), nullable=True))
        batch.add_column(sa.Column('deleted_at', UTCDatetime(), nullable=True))
        batch.add_column(sa.Column('client_mutation_id', sa.String(80), nullable=True))
    op.create_index('uq_comments_mutation', 'comments', ['user_id', 'client_mutation_id'], unique=True)
    op.create_index('ix_comments_post_root_id', 'comments', ['post_id', 'root_id', 'id'])

    op.create_table('post_bookmarks',
                    sa.Column('post_id', sa.Integer(), sa.ForeignKey('posts.id'), primary_key=True),
                    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
                    sa.Column('created_at', UTCDatetime(), nullable=False))
    op.create_table('post_media',
                    sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                    sa.Column('post_id', sa.Integer(), sa.ForeignKey('posts.id'), nullable=False),
                    sa.Column('position', sa.Integer(), nullable=False),
                    sa.Column('path', sa.String(255), nullable=False),
                    sa.Column('kind', sa.String(16), nullable=False),
                    sa.UniqueConstraint('post_id', 'position', name='uq_post_media_position'))
    op.create_index('ix_post_media_post_id', 'post_media', ['post_id'])
    op.create_table('stories',
                    sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
                    sa.Column('image_path', sa.String(255), nullable=False),
                    sa.Column('caption', sa.String(300), nullable=False, server_default=''),
                    sa.Column('audience', sa.String(16), nullable=False, server_default='followers'),
                    sa.Column('created_at', UTCDatetime(), nullable=False),
                    sa.Column('expires_at', UTCDatetime(), nullable=False),
                    sa.Column('deleted_at', UTCDatetime(), nullable=True),
                    sa.Column('client_mutation_id', sa.String(80), nullable=True))
    op.create_index('ix_stories_user_id', 'stories', ['user_id'])
    op.create_index('ix_stories_expires_at', 'stories', ['expires_at'])
    op.create_index('uq_stories_mutation', 'stories', ['user_id', 'client_mutation_id'], unique=True)
    op.create_table('story_views',
                    sa.Column('story_id', sa.Integer(), sa.ForeignKey('stories.id'), primary_key=True),
                    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
                    sa.Column('viewed_at', UTCDatetime(), nullable=False))
    op.create_table('discovery_locations',
                    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
                    sa.Column('latitude_grid', sa.Float(), nullable=False),
                    sa.Column('longitude_grid', sa.Float(), nullable=False),
                    sa.Column('accuracy_meters', sa.Float(), nullable=False),
                    sa.Column('observed_at', UTCDatetime(), nullable=False),
                    sa.Column('expires_at', UTCDatetime(), nullable=False),
                    sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('discovery_queries',
                    sa.Column('id', sa.String(48), primary_key=True),
                    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
                    sa.Column('center_latitude_grid', sa.Float(), nullable=False),
                    sa.Column('center_longitude_grid', sa.Float(), nullable=False),
                    sa.Column('radius_km', sa.Integer(), nullable=False),
                    sa.Column('candidate_ids_json', sa.Text(), nullable=False),
                    sa.Column('created_at', UTCDatetime(), nullable=False),
                    sa.Column('expires_at', UTCDatetime(), nullable=False))
    op.create_index('ix_discovery_queries_user_id', 'discovery_queries', ['user_id'])
    op.create_table('social_notifications',
                    sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                    sa.Column('recipient_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
                    sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
                    sa.Column('kind', sa.String(24), nullable=False),
                    sa.Column('post_id', sa.Integer(), nullable=True),
                    sa.Column('comment_id', sa.Integer(), nullable=True),
                    sa.Column('created_at', UTCDatetime(), nullable=False),
                    sa.Column('read_at', UTCDatetime(), nullable=True),
                    sa.Column('dedup_key', sa.String(160), nullable=False, unique=True))
    op.create_index('ix_social_notifications_recipient_id', 'social_notifications', ['recipient_id'])
    op.create_table('social_outbox',
                    sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                    sa.Column('event_key', sa.String(160), nullable=False, unique=True),
                    sa.Column('kind', sa.String(24), nullable=False),
                    sa.Column('payload_json', sa.Text(), nullable=False),
                    sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
                    sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
                    sa.Column('next_attempt_at', UTCDatetime(), nullable=False),
                    sa.Column('lease_until', UTCDatetime(), nullable=True))


def downgrade():
    op.drop_table('social_outbox')
    op.drop_index('ix_social_notifications_recipient_id', table_name='social_notifications')
    op.drop_table('social_notifications')
    op.drop_index('ix_discovery_queries_user_id', table_name='discovery_queries')
    op.drop_table('discovery_queries')
    op.drop_table('discovery_locations')
    op.drop_table('story_views')
    op.drop_index('uq_stories_mutation', table_name='stories')
    op.drop_index('ix_stories_expires_at', table_name='stories')
    op.drop_index('ix_stories_user_id', table_name='stories')
    op.drop_table('stories')
    op.drop_index('ix_post_media_post_id', table_name='post_media')
    op.drop_table('post_media')
    op.drop_table('post_bookmarks')
    op.drop_index('ix_comments_post_root_id', table_name='comments')
    op.drop_index('uq_comments_mutation', table_name='comments')
    with op.batch_alter_table('comments') as batch:
        batch.drop_column('client_mutation_id')
        batch.drop_column('deleted_at')
        batch.drop_column('reply_to_comment_id')
        batch.drop_column('root_id')
    op.drop_index('uq_posts_mutation', table_name='posts')
    with op.batch_alter_table('posts') as batch:
        batch.drop_column('client_mutation_id')
        batch.drop_column('version')
        batch.drop_column('audience')
        batch.drop_column('deleted_at')
        batch.drop_column('updated_at')
    op.drop_column('users', 'profile_version')
