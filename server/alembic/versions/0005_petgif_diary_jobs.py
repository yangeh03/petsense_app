"""Persist asynchronous PetGIF diary jobs."""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'petgif_diary_jobs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('pet_id', sa.Integer(), nullable=False),
        sa.Column('petgif_id', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('input_json', sa.Text(), nullable=False),
        sa.Column('photo_paths_json', sa.Text(), nullable=False),
        sa.Column('upstream_diary_id', sa.String(length=128), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=False),
        sa.Column('error_message', sa.String(length=240), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
        sa.Column('finished_at', UTCDatetime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['pet_id'], ['pets.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'pet_id', 'idempotency_key', name='uq_petgif_diary_idempotency'),
    )
    op.create_index('ix_petgif_diary_jobs_user_id', 'petgif_diary_jobs', ['user_id'])
    op.create_index('ix_petgif_diary_jobs_pet_id', 'petgif_diary_jobs', ['pet_id'])
    op.create_index('ix_petgif_diary_jobs_pet_created', 'petgif_diary_jobs', ['pet_id', 'created_at'])


def downgrade():
    op.drop_index('ix_petgif_diary_jobs_pet_created', table_name='petgif_diary_jobs')
    op.drop_index('ix_petgif_diary_jobs_pet_id', table_name='petgif_diary_jobs')
    op.drop_index('ix_petgif_diary_jobs_user_id', table_name='petgif_diary_jobs')
    op.drop_table('petgif_diary_jobs')
