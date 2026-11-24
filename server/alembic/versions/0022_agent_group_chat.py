"""双 Agent 群聊：回复消息、路由记录与运行关联。

接 W13 的 0021（单 head：… → 0020 → 0021 → 0022）。

- agent_messages：按回合归属的回复记录（稳定 ID、角色、顺序、状态、正文、来源、内容块、
  单调版本、副作用日志）；每回合每位至多一条（turn_id + role 唯一）。
- agent_routes：一次主人消息的路由结果（范围、意图、发言者计划），重试沿用。
- agent_turns.reply_to_message：消息级引用；agent_turns.pending_at：群聊回合重试后重新开始等待的时间。
- agent_runs.message_id：运行轨迹关联到它产出的那一条回复。
"""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_routes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('turn_id', sa.Integer(), nullable=False),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('scope', sa.String(16), nullable=False),
        sa.Column('intent', sa.String(16), nullable=False),
        sa.Column('companion', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('professional', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('follows', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('plan_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('raw_json', sa.Text(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.UniqueConstraint('turn_id'),
    )
    op.create_index('ix_agent_routes_pet_id', 'agent_routes', ['pet_id'])
    op.create_table(
        'agent_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('turn_id', sa.Integer(), nullable=False),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('follows', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sources', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('blocks', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('effect_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('started_at', UTCDatetime(), nullable=True),
        sa.Column('finished_at', UTCDatetime(), nullable=True),
        sa.UniqueConstraint('turn_id', 'role'),
    )
    op.create_index('ix_agent_messages_turn_id', 'agent_messages', ['turn_id'])
    op.create_index('ix_agent_messages_pet_id', 'agent_messages', ['pet_id'])
    op.add_column('agent_turns', sa.Column('reply_to_message', sa.String(32), nullable=True))
    op.add_column('agent_turns', sa.Column('pending_at', UTCDatetime(), nullable=True))
    op.add_column('agent_runs', sa.Column('message_id', sa.Integer(), nullable=True))
    op.create_index('ix_agent_runs_message_id', 'agent_runs', ['message_id'])


def downgrade():
    op.drop_index('ix_agent_runs_message_id', table_name='agent_runs')
    op.drop_column('agent_runs', 'message_id')
    op.drop_column('agent_turns', 'pending_at')
    op.drop_column('agent_turns', 'reply_to_message')
    op.drop_table('agent_messages')
    op.drop_table('agent_routes')
