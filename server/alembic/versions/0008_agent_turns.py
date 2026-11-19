"""Durable companion turns."""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_turns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('request_id', sa.String(80), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('target', sa.String(16), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=True),
        sa.Column('reply_to', sa.Integer(), nullable=True),
        sa.Column('sources', sa.Text(), nullable=False),
        sa.Column('speaker', sa.String(16), nullable=False),
        sa.Column('read_at', UTCDatetime(), nullable=True),
        sa.Column('reply', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.UniqueConstraint('pet_id', 'request_id'))
    op.create_index('ix_agent_turns_pet_id', 'agent_turns', ['pet_id'])
    op.add_column('telemetry_minutes', sa.Column('source_versions', sa.Text(), nullable=False, server_default='[]'))
    op.add_column('telemetry_minutes', sa.Column('sound_observed_seconds', sa.Float(), nullable=False, server_default='0'))
    op.create_table('agent_learning_days',
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), primary_key=True),
        sa.Column('device_id', sa.String(64), primary_key=True),
        sa.Column('feature', sa.String(16), primary_key=True),
        sa.Column('version', sa.String(80), primary_key=True),
        sa.Column('day', sa.String(10), primary_key=True),
        sa.Column('seconds', sa.Float(), nullable=False),
        sa.Column('last_seen_at', UTCDatetime(), nullable=False),
        sa.Column('slots', sa.Text(), nullable=False))


def downgrade():
    with op.batch_alter_table('telemetry_minutes') as batch:
        batch.drop_column('source_versions')
        batch.drop_column('sound_observed_seconds')
    op.drop_table('agent_learning_days')
    op.drop_table('agent_turns')
