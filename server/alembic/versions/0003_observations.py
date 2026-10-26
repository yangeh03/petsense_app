"""Durable product observations, independent of the raw telemetry ring buffer."""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('telemetry_latest',
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(64), primary_key=True),
        sa.Column('boot_id', sa.String(80), nullable=False),
        sa.Column('ordinal', sa.BigInteger(), nullable=False),
        sa.Column('retired_boots', sa.Text(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('received_at', UTCDatetime(), nullable=False))
    op.create_table('telemetry_minutes',
        sa.Column('owner_id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.String(64), primary_key=True),
        sa.Column('minute', UTCDatetime(), primary_key=True),
        sa.Column('metrics', sa.Text(), nullable=False),
        sa.Column('actions', sa.Text(), nullable=False),
        sa.Column('observed_seconds', sa.Float(), nullable=False),
        sa.Column('frames', sa.Integer(), nullable=False))
    op.create_table('telemetry_events',
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('device_id', sa.String(64), nullable=False),
        sa.Column('boot_id', sa.String(80), nullable=False),
        sa.Column('model_version', sa.String(80), nullable=False),
        sa.Column('label', sa.String(32), nullable=False),
        sa.Column('started_at', UTCDatetime(), nullable=False),
        sa.Column('last_seen_at', UTCDatetime(), nullable=False),
        sa.Column('frames', sa.Integer(), nullable=False),
        sa.Column('confidence_sum', sa.Float(), nullable=False),
        sa.Column('observed_seconds', sa.Float(), nullable=False),
        sa.Column('experimental', sa.Integer(), nullable=False))
    op.create_index('ix_telemetry_events_device_time', 'telemetry_events', ['device_id', 'last_seen_at'])


def downgrade():
    op.drop_table('telemetry_events')
    op.drop_table('telemetry_minutes')
    op.drop_table('telemetry_latest')
