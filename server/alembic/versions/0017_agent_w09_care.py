"""Agent W09: proactive care topics, hardware rule watermarks and review enablement.

接 W08 的 0016（单 head：0014 → 0015 → 0016 → 0017）。
"""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_care_topics',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('category', sa.String(32), nullable=False),
        sa.Column('anchor', sa.String(160), nullable=False),
        sa.Column('instance', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('severity', sa.String(16), nullable=False),
        sa.Column('title', sa.String(80), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('delivered', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('opened_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
        sa.Column('recovered_at', UTCDatetime(), nullable=True),
        sa.Column('closed_reason', sa.String(120), nullable=True))
    op.create_index('ix_agent_care_topics_pet_id', 'agent_care_topics', ['pet_id'])
    op.create_index('uq_agent_care_topics_instance', 'agent_care_topics',
                    ['pet_id', 'kind', 'category', 'anchor', 'instance'], unique=True)
    op.create_table('care_device_states',
        sa.Column('device_id', sa.String(64), primary_key=True),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('battery_violation', sa.Integer(), nullable=False),
        sa.Column('battery_recovery', sa.Integer(), nullable=False),
        sa.Column('battery_first_at', UTCDatetime(), nullable=True),
        sa.Column('battery_critical', sa.Integer(), nullable=False),
        sa.Column('invalid_streak', sa.Integer(), nullable=False),
        sa.Column('invalid_first_at', UTCDatetime(), nullable=True),
        sa.Column('last_frame_at', UTCDatetime(), nullable=True),
        sa.Column('last_battery_percent', sa.Float(), nullable=True),
        sa.Column('frames_seen', sa.Integer(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False))
    op.create_table('care_pet_states',
        sa.Column('pet_id', sa.Integer(), primary_key=True),
        sa.Column('enabled_at', UTCDatetime(), nullable=False),
        sa.Column('learning_watermark', sa.Text(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False))


def downgrade():
    op.drop_table('care_pet_states')
    op.drop_table('care_device_states')
    op.drop_index('uq_agent_care_topics_instance', table_name='agent_care_topics')
    op.drop_index('ix_agent_care_topics_pet_id', table_name='agent_care_topics')
    op.drop_table('agent_care_topics')
