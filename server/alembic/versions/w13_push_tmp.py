"""W13 原生推送（iOS 首版）：安装实例、用户开关与投递审计表。

⚠️ 临时修订号，合入时必须改号（见 docs/agent-w13-spec.md §实施记录）。

本工作树的上一版迁移是 0019；W12 正在并行实现 0020。W13 按负责人指示**在本工作树
内临时接在 0019 之后**，以保证本分支可独立跑通全量单测与 alembic 往返。

合入主干时必须调整为：

    revision = '0021'                # 0020 已被 W12 占用，取下一个空号
    down_revision = '0020'           # 指向 W12 的头
    文件名 → 0021_agent_w13_push.py

不改 down_revision 会在主干产生两个 head（0019 → w13_push_tmp 与 0019 → 0020），
alembic upgrade head 行为不确定。改号后本文件内容无需其他调整。

创建三张表：push_preferences / push_installations / push_deliveries。
"""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime

revision = 'w13_push_tmp'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'push_preferences',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('enabled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
    )
    op.create_table(
        'push_installations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('installation_id', sa.String(64), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('expo_token', sa.String(255), nullable=False),
        sa.Column('token_digest', sa.String(64), nullable=False),
        sa.Column('previous_token_digest', sa.String(64), nullable=True),
        sa.Column('token_version', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('permission_status', sa.String(16), nullable=False),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
        sa.Column('last_seen_at', UTCDatetime(), nullable=True),
        sa.UniqueConstraint('installation_id'),
    )
    op.create_index('ix_push_installations_user_id', 'push_installations', ['user_id'])
    # 同一个 Expo token 只能绑定一个安装实例（清空后的空串不参与唯一约束）
    op.create_index('uq_push_installations_token', 'push_installations', ['expo_token'],
                    unique=True, sqlite_where=sa.text("expo_token != ''"),
                    postgresql_where=sa.text("expo_token != ''"))
    op.create_table(
        'push_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('dedup_key', sa.String(160), nullable=False),
        sa.Column('inbox_item_id', sa.String(64), nullable=False),
        sa.Column('installation_id', sa.String(64), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('category', sa.String(24), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('reason', sa.String(64), nullable=True),
        sa.Column('expo_ticket_id', sa.String(64), nullable=True),
        sa.Column('expo_receipt_id', sa.String(64), nullable=True),
        sa.Column('ticket_status', sa.String(16), nullable=True),
        sa.Column('receipt_status', sa.String(16), nullable=True),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', UTCDatetime(), nullable=True),
        sa.Column('payload_digest', sa.String(64), nullable=True),
        sa.Column('token_digest', sa.String(64), nullable=True),
        sa.Column('token_version', sa.Integer(), nullable=True),
        sa.Column('meta', sa.Text(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
        sa.Column('sent_at', UTCDatetime(), nullable=True),
        sa.Column('finished_at', UTCDatetime(), nullable=True),
        sa.UniqueConstraint('dedup_key'),
    )
    op.create_index('ix_push_deliveries_inbox_item_id', 'push_deliveries', ['inbox_item_id'])
    op.create_index('ix_push_deliveries_installation_id', 'push_deliveries', ['installation_id'])
    op.create_index('ix_push_deliveries_user_id', 'push_deliveries', ['user_id'])
    op.create_index('ix_push_deliveries_state_next', 'push_deliveries',
                    ['state', 'next_attempt_at'])


def downgrade():
    op.drop_table('push_deliveries')
    op.drop_table('push_installations')
    op.drop_table('push_preferences')
