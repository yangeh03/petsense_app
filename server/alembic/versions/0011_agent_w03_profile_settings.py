"""Agent W03: pet birthday, weight records, companion settings and proposals."""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime
revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pets', sa.Column('birth_date', sa.Date(), nullable=True))
    op.add_column('pets', sa.Column('birth_date_precision', sa.String(8), nullable=True))
    op.create_table('pet_weight_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('measured_on', sa.Date(), nullable=False),
        sa.Column('weight_g', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('raw_text', sa.String(40), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.UniqueConstraint('pet_id', 'measured_on'))
    op.create_index('ix_pet_weight_records_pet_id', 'pet_weight_records', ['pet_id'])
    op.create_table('companion_settings',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('assistant_name', sa.String(20), nullable=False),
        sa.Column('owner_title', sa.String(20), nullable=True),
        sa.Column('verbosity', sa.String(8), nullable=False),
        sa.Column('proactivity', sa.Integer(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False))
    op.create_table('agent_proposals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('turn_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('superseded_by', sa.Integer(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('decided_at', UTCDatetime(), nullable=True))
    op.create_index('ix_agent_proposals_pet_id', 'agent_proposals', ['pet_id'])


def downgrade():
    op.drop_table('agent_proposals')
    op.drop_table('companion_settings')
    op.drop_table('pet_weight_records')
    with op.batch_alter_table('pets') as batch:
        batch.drop_column('birth_date_precision')
        batch.drop_column('birth_date')
