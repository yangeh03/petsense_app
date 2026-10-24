"""设备归属：devices 表 + readings.device_id + pets 一设备一宠物唯一索引。

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25
"""
import petsense.db.base
import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('devices',
        sa.Column('device_id', sa.String(length=64), primary_key=True),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('claimed_at', petsense.db.base.UTCDatetime(), nullable=False),
        sa.Column('last_seen_at', petsense.db.base.UTCDatetime(), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']))
    op.create_index('ix_devices_owner_id', 'devices', ['owner_id'], unique=True)
    with op.batch_alter_table('readings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_readings_device_received', ['device_id', 'received_at'])
    op.create_index('uq_pets_device_id', 'pets', ['device_id'], unique=True,
                    sqlite_where=sa.text("device_id != ''"),
                    postgresql_where=sa.text("device_id != ''"))


def downgrade() -> None:
    op.drop_index('uq_pets_device_id', table_name='pets')
    with op.batch_alter_table('readings', schema=None) as batch_op:
        batch_op.drop_index('ix_readings_device_received')
        batch_op.drop_column('device_id')
    op.drop_index('ix_devices_owner_id', table_name='devices')
    op.drop_table('devices')
