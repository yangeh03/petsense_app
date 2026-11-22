"""Agent run trajectories and turn content blocks (W01)."""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('turn_id', sa.Integer(), nullable=True),
        sa.Column('form', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('conclusion', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('messages', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('tool_summary', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('budget', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False))
    op.create_index('ix_agent_runs_pet_id', 'agent_runs', ['pet_id'])
    op.add_column('agent_turns', sa.Column('blocks', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('agent_turns') as batch:
        batch.drop_column('blocks')
    op.drop_index('ix_agent_runs_pet_id', table_name='agent_runs')
    op.drop_table('agent_runs')
