"""Agent run snapshot column (W02): resident snapshot for numeric traceability."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_runs', sa.Column('snapshot', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('agent_runs') as batch:
        batch.drop_column('snapshot')
