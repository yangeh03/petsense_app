"""Agent W10: action receipts for device recording, diary and social drafts.

接 W09 的 0017（单 head：0014 → 0015 → 0016 → 0017 → 0018）。
"""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_actions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('proposal_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('device_id', sa.String(64), nullable=True),
        sa.Column('request_token', sa.String(64), nullable=True),
        sa.Column('intent_key', sa.String(64), nullable=True),
        sa.Column('duration_sec', sa.Integer(), nullable=True),
        sa.Column('input_json', sa.Text(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
        sa.Column('finished_at', UTCDatetime(), nullable=True),
        sa.UniqueConstraint('request_token'),
        sa.UniqueConstraint('intent_key'))
    op.create_index('ix_agent_actions_pet_id', 'agent_actions', ['pet_id'])
    op.create_index('ix_agent_actions_pet_kind_id', 'agent_actions', ['pet_id', 'kind', 'id'])


def downgrade():
    op.drop_table('agent_actions')
