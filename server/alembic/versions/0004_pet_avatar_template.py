"""Store the reusable PetGIF animation choice separately from personal identity."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pets', sa.Column('avatar_template_id', sa.String(length=32), nullable=True))


def downgrade():
    op.drop_column('pets', 'avatar_template_id')
