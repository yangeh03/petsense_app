"""PetMemory long-term store (W04): tiered memory table + FTS5 trigram index."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pet_memories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('subject', sa.String(16), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('facts', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('source_refs', sa.Text(), nullable=False),
        sa.Column('support_snapshot', sa.Text(), nullable=True),
        sa.Column('source_type', sa.String(16), nullable=False),
        sa.Column('write_level', sa.String(16), nullable=False),
        sa.Column('confirmation', sa.Text(), nullable=True),
        sa.Column('certainty', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('supersedes_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_pet_memories_pet_id', 'pet_memories', ['pet_id'])
    op.create_index('ix_pet_memories_kind_status', 'pet_memories', ['kind', 'status'])
    if op.get_bind().dialect.name == 'sqlite':
        # FTS5 trigram：中文子串召回（≥3 字关键词；短词由存储层回退 LIKE）。
        # external content + rowid=id；status 变化不动物理索引，查询时过滤 active。
        op.execute("CREATE VIRTUAL TABLE pet_memories_fts USING fts5("
                   "summary, content='pet_memories', content_rowid='id', tokenize='trigram')")
        op.execute("CREATE TRIGGER pet_memories_fts_ai AFTER INSERT ON pet_memories BEGIN "
                   "INSERT INTO pet_memories_fts(rowid, summary) VALUES (new.id, new.summary); END")
        op.execute("CREATE TRIGGER pet_memories_fts_au AFTER UPDATE OF summary ON pet_memories BEGIN "
                   "INSERT INTO pet_memories_fts(pet_memories_fts, rowid, summary) "
                   "VALUES ('delete', old.id, old.summary); "
                   "INSERT INTO pet_memories_fts(rowid, summary) VALUES (new.id, new.summary); END")
        op.execute("CREATE TRIGGER pet_memories_fts_ad AFTER DELETE ON pet_memories BEGIN "
                   "INSERT INTO pet_memories_fts(pet_memories_fts, rowid, summary) "
                   "VALUES ('delete', old.id, old.summary); END")


def downgrade():
    if op.get_bind().dialect.name == 'sqlite':
        op.execute('DROP TRIGGER IF EXISTS pet_memories_fts_ad')
        op.execute('DROP TRIGGER IF EXISTS pet_memories_fts_au')
        op.execute('DROP TRIGGER IF EXISTS pet_memories_fts_ai')
        op.execute('DROP TABLE IF EXISTS pet_memories_fts')
    op.drop_index('ix_pet_memories_kind_status', table_name='pet_memories')
    op.drop_index('ix_pet_memories_pet_id', table_name='pet_memories')
    op.drop_table('pet_memories')
