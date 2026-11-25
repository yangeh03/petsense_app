"""W16 多会话（docs/agent-w16-spec.md §4）：会话表，回合、关怀主题与委托关联到会话。

接双 Agent 群聊的 0022（单 head：… → 0021 → 0022 → 0023）。

- agent_conversations：一只宠物名下多个彼此独立的对话。kind=chat 是主人的会话，
  kind=care 是主动消息自带的系统会话；软删除。
- agent_turns.conversation_id：回合所属会话。列保持可空（不在 SQLite 上重建本表），
  但本迁移把存量全部回填，结束时断言没有空值，之后由代码保证必填。
- agent_care_topics.conversation_id：设备/学习类主题的系统会话，首次交付时回填（唯一）。
- agent_tasks.conversation_id：确认该委托的回合所在会话。

存量：每只有回合的宠物建一行「历史聊天」，全部旧回合与现有委托指向它。不按旧话题拆分：
旧 topic_id 一列混用了回合 id 和关怀主题 id，无法可靠区分。旧列 agent_turns.topic_id 与
agent_tasks.topic_id 保留在库里，不再读写。
"""
from alembic import op
import sqlalchemy as sa
from petsense.db.base import UTCDatetime

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None

LEGACY_TITLE = '历史聊天'


def _tables():
    """回填只用到这几列；在迁移内自带定义，不依赖之后可能变化的 ORM 模型。"""
    meta = sa.MetaData()
    conversations = sa.Table(
        'agent_conversations', meta,
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pet_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('title', sa.String(80), nullable=False),
        sa.Column('title_custom', sa.Integer(), nullable=False),
        sa.Column('last_turn_at', UTCDatetime(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
    )
    turns = sa.Table(
        'agent_turns', meta,
        sa.Column('pet_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
    )
    tasks = sa.Table(
        'agent_tasks', meta,
        sa.Column('pet_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=True),
    )
    return conversations, turns, tasks


def _backfill():
    bind = op.get_bind()
    conversations, turns, tasks = _tables()
    pets = bind.execute(sa.select(
        turns.c.pet_id, sa.func.min(turns.c.created_at), sa.func.max(turns.c.created_at),
    ).group_by(turns.c.pet_id)).fetchall()
    for pet_id, first, last in pets:
        conversation_id = bind.execute(conversations.insert().values(
            pet_id=pet_id, kind='chat', title=LEGACY_TITLE, title_custom=0,
            last_turn_at=last, created_at=first, updated_at=last,
        )).inserted_primary_key[0]
        bind.execute(turns.update().where(turns.c.pet_id == pet_id)
                     .values(conversation_id=conversation_id))
        bind.execute(tasks.update().where(tasks.c.pet_id == pet_id)
                     .values(conversation_id=conversation_id))
    orphan = bind.execute(sa.select(sa.func.count()).select_from(turns)
                          .where(turns.c.conversation_id.is_(None))).scalar_one()
    if orphan:
        raise RuntimeError('W16 迁移：仍有 %d 条回合没有归属会话' % orphan)


def upgrade():
    op.create_table(
        'agent_conversations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('pet_id', sa.Integer(), sa.ForeignKey('pets.id'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('care_kind', sa.String(16), nullable=True),
        sa.Column('title', sa.String(80), nullable=False, server_default=''),
        sa.Column('title_custom', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_turn_at', UTCDatetime(), nullable=True),
        sa.Column('deleted_at', UTCDatetime(), nullable=True),
        sa.Column('created_at', UTCDatetime(), nullable=False),
        sa.Column('updated_at', UTCDatetime(), nullable=False),
    )
    op.create_index('ix_agent_conversations_pet_id', 'agent_conversations', ['pet_id'])
    op.add_column('agent_turns', sa.Column('conversation_id', sa.Integer(), nullable=True))
    op.create_index('ix_agent_turns_conversation_id', 'agent_turns', ['conversation_id'])
    op.add_column('agent_care_topics', sa.Column('conversation_id', sa.Integer(), nullable=True))
    op.create_index('uq_agent_care_topics_conversation_id', 'agent_care_topics', ['conversation_id'],
                    unique=True)
    op.add_column('agent_tasks', sa.Column('conversation_id', sa.Integer(), nullable=True))
    op.create_index('ix_agent_tasks_conversation_id', 'agent_tasks', ['conversation_id'])
    _backfill()


def downgrade():
    op.drop_index('ix_agent_tasks_conversation_id', table_name='agent_tasks')
    op.drop_column('agent_tasks', 'conversation_id')
    op.drop_index('uq_agent_care_topics_conversation_id', table_name='agent_care_topics')
    op.drop_column('agent_care_topics', 'conversation_id')
    op.drop_index('ix_agent_turns_conversation_id', table_name='agent_turns')
    op.drop_column('agent_turns', 'conversation_id')
    op.drop_table('agent_conversations')
