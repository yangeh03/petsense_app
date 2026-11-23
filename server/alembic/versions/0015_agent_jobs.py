"""Agent W08: generic durable job queue for background runs (W09 reuse)."""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('dedup_key', sa.String(120), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('next_run_at', UTCDatetime(), nullable=False),
        sa.Column('lease_until', UTCDatetime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False))
    op.create_index('ix_agent_jobs_dedup_key', 'agent_jobs', ['dedup_key'], unique=True)
    op.create_index('ix_agent_jobs_status_next_run_at', 'agent_jobs', ['status', 'next_run_at'])


def downgrade():
    op.drop_index('ix_agent_jobs_status_next_run_at', table_name='agent_jobs')
    op.drop_index('ix_agent_jobs_dedup_key', table_name='agent_jobs')
    op.drop_table('agent_jobs')
