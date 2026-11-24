"""Agent W12: durable inbox index and projection watermarks.

接 W10 的 0018 与 0019（单 head：0018 → 0019 → 0020）。
"""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_inbox_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('pet_id', sa.Integer(), nullable=False),
        sa.Column('source_kind', sa.String(16), nullable=False),
        sa.Column('event_key', sa.String(120), nullable=False),
        sa.Column('source_ref', sa.Text(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('previewable', sa.Integer(), nullable=False),
        sa.Column('published', sa.Integer(), nullable=False),
        sa.Column('turn_id', sa.Integer(), nullable=True),
        sa.Column('relevance', sa.String(16), nullable=False),
        sa.Column('frozen_evidence', sa.Integer(), nullable=False),
        sa.Column('delivery_approved', sa.Integer(), nullable=False),
        sa.Column('high_relevance', sa.Integer(), nullable=False),
        sa.Column('push_candidate', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('backfilled', sa.Integer(), nullable=False),
        sa.Column('read_at', UTCDatetime(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False))
    op.create_index('ix_agent_inbox_items_user_id', 'agent_inbox_items', ['user_id'])
    op.create_index('ix_agent_inbox_items_pet_id', 'agent_inbox_items', ['pet_id'])
    op.create_index('uq_agent_inbox_items_source', 'agent_inbox_items',
                    ['pet_id', 'source_kind', 'event_key'], unique=True)
    op.create_table('agent_inbox_states',
        sa.Column('pet_id', sa.Integer(), primary_key=True),
        sa.Column('enabled_at', UTCDatetime(), nullable=False),
        sa.Column('care_watermark', sa.Integer(), nullable=False),
        sa.Column('delivery_watermark', sa.Integer(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False))


def downgrade():
    op.drop_table('agent_inbox_states')
    op.drop_index('uq_agent_inbox_items_source', table_name='agent_inbox_items')
    op.drop_index('ix_agent_inbox_items_pet_id', table_name='agent_inbox_items')
    op.drop_index('ix_agent_inbox_items_user_id', table_name='agent_inbox_items')
    op.drop_table('agent_inbox_items')
