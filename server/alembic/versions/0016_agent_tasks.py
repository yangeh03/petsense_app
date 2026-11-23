"""Agent W08: delegation tasks with append-only plan versions and deliveries."""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('task_key', sa.String(40), nullable=False),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('timezone', sa.String(40), nullable=False),
        sa.Column('deadline_at', UTCDatetime(), nullable=False),
        sa.Column('effective_from', UTCDatetime(), nullable=False),
        sa.Column('observing_since', UTCDatetime(), nullable=True),
        sa.Column('paused_intervals', sa.Text(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=True),
        sa.Column('proposal_id', sa.Integer(), nullable=True),
        sa.Column('last_check_at', UTCDatetime(), nullable=True),
        sa.Column('watermark_at', UTCDatetime(), nullable=True),
        sa.Column('due_attempts', sa.Integer(), nullable=False),
        sa.Column('ended_at', UTCDatetime(), nullable=True),
        sa.Column('end_reason', sa.String(24), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
        sa.UniqueConstraint('pet_id', 'task_key'))
    op.create_index('ix_agent_tasks_pet_id', 'agent_tasks', ['pet_id'])
    op.create_table('agent_task_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('agent_tasks.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('proposal_id', sa.Integer(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.UniqueConstraint('task_id', 'version'))
    op.create_index('ix_agent_task_plans_task_id', 'agent_task_plans', ['task_id'])
    op.create_table('agent_task_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('agent_tasks.id'), nullable=False),
        sa.Column('plan_version', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('instance_key', sa.String(64), nullable=False),
        sa.Column('turn_id', sa.Integer(), nullable=True),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('archived_memory_id', sa.Integer(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.UniqueConstraint('task_id', 'instance_key'))
    op.create_index('ix_agent_task_deliveries_task_id', 'agent_task_deliveries', ['task_id'])


def downgrade():
    op.drop_index('ix_agent_task_deliveries_task_id', table_name='agent_task_deliveries')
    op.drop_table('agent_task_deliveries')
    op.drop_index('ix_agent_task_plans_task_id', table_name='agent_task_plans')
    op.drop_table('agent_task_plans')
    op.drop_index('ix_agent_tasks_pet_id', table_name='agent_tasks')
    op.drop_table('agent_tasks')
