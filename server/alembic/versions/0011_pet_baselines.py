"""Pet baseline table (W05): materialized per-dimension reference ranges."""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pet_baselines',
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), primary_key=True),
        sa.Column('feature', sa.String(16), primary_key=True),
        sa.Column('version', sa.String(80), primary_key=True),
        sa.Column('state', sa.String(16), nullable=False, server_default='learning'),
        sa.Column('valid_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('window_last_day', sa.String(10), nullable=False, server_default=''),
        sa.Column('sample_days', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('day_values', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('day_stats', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('slot_stats', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('pet_baselines')
