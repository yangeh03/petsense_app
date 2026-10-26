"""Track PetGIF identities created for an authenticated PetSense account."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'petgif_identities',
        sa.Column('petgif_id', sa.String(length=128), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_petgif_identities_user_id', 'petgif_identities', ['user_id'])
def downgrade():
    op.drop_index('ix_petgif_identities_user_id', table_name='petgif_identities')
    op.drop_table('petgif_identities')
