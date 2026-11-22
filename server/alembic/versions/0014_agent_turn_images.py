"""Agent turn images column (W07): owner chat attachments as JSON references."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_turns', sa.Column('images', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('agent_turns') as batch:
        batch.drop_column('images')
