"""W16 会话域（docs/agent-w16-spec.md §5）：会话的建立、标题、序列化、历史查询与删除前检查。

一只宠物名下有多个彼此独立的会话，模型历史只取当前会话（history）。kind=chat 是主人的会话；
kind=care 是主动消息自带的系统会话（设备告警、学习完成按主题一个，周期回顾每条汇总一个）。
删除是软删除：回合、轨迹与记忆都保留；会话里还有进行中的委托或未结束的关怀主题时拒绝删除。
"""
from datetime import datetime, timedelta

from sqlalchemy import case, func, select

from petsense.db import iso_z, session_scope, utc_now
from petsense.db.models.agent import AgentCareTopic, AgentConversation, AgentTask, AgentTurn

KIND_CHAT = 'chat'
KIND_CARE = 'care'
CARE_KINDS = ('device', 'learning', 'review', 'task')  # task：委托没有会话时兜底建立的会话
LEGACY_TITLE = '历史聊天'
ACTIVE_TASK_STATUSES = ('waiting_baseline', 'observing', 'paused')
OPEN_TOPIC_STATUSES = ('open', 'ongoing')
AUTO_TITLE_CHARS = 20
MAX_CUSTOM_TITLE = 40
PREVIEW_CHARS = 40
LIST_PAGE = 50
TURNS_IN_READ_RESPONSE = 40
TOPIC_STATUS_LABELS = {'open': '进行中', 'ongoing': '进行中', 'recovered': '已恢复', 'closed': '已结束'}


class ConversationError(Exception):
    def __init__(self, status, message, code=None, **extra):
        super().__init__(message)
        self.status, self.message, self.code, self.extra = status, message, code, extra


# ── 查找与建立 ────────────────────────────────────────────────────────────

def visible(session, conversation_id, pet_id):
    """本人宠物名下、未删除的会话；不存在或不可用时返回 None。"""
    if isinstance(conversation_id, bool) or not isinstance(conversation_id, int):
        return None
    row = session.get(AgentConversation, conversation_id)
    if row is None or row.pet_id != pet_id or row.deleted_at is not None:
        return None
    return row


def turn_count(session, conversation_id):
    return session.execute(select(func.count()).select_from(AgentTurn).where(
        AgentTurn.conversation_id == conversation_id)).scalar_one()


def create_chat(session, pet, now=None):
    """新建主人会话，返回 (会话, 是否新建)。

    最近一个仍为空且未改名的主人会话直接复用：连点「新对话」不会留下一串空会话。
    """
    now = now or utc_now()
    candidates = session.execute(select(AgentConversation).where(
        AgentConversation.pet_id == pet.id, AgentConversation.kind == KIND_CHAT,
        AgentConversation.deleted_at.is_(None), AgentConversation.title_custom == 0,
    ).order_by(AgentConversation.id.desc()).limit(5)).scalars().all()
    for row in candidates:
        if turn_count(session, row.id) == 0:
            return row, False
    row = AgentConversation(pet_id=pet.id, kind=KIND_CHAT, title='', title_custom=0,
                            created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return row, True


def create_care(session, pet_id, care_kind, title, now=None):
    """主动消息自带的系统会话。"""
    now = now or utc_now()
    row = AgentConversation(pet_id=pet_id, kind=KIND_CARE, care_kind=care_kind,
                            title=(title or '')[:80], title_custom=0, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return row


def for_topic(session, topic, now=None):
    """设备/学习类关怀主题的系统会话：首次交付时建立并回填 topic.conversation_id，之后复用。"""
    if topic.conversation_id is not None:
        row = session.get(AgentConversation, topic.conversation_id)
        if row is not None:
            return row
    row = create_care(session, topic.pet_id, topic.kind, topic.title, now)
    topic.conversation_id = row.id
    return row


def review_title(kind, key):
    """周期回顾会话的标题。key 是宠物时区下的窗口日期文本：日回顾为当天，周回顾为那一周的周一。"""
    day = datetime.strptime(key, '%Y-%m-%d')
    if kind == 'weekly':
        end = day + timedelta(days=6)
        return '本周回顾 · %d/%d–%d/%d' % (day.month, day.day, end.month, end.day)
    return '观察小结 · %d月%d日' % (day.month, day.day)


# ── 标题与活动时间 ────────────────────────────────────────────────────────

def auto_title(conversation, text):
    """主人会话的第一条消息决定标题（纯图片用固定文案）；改过名的不动。"""
    if conversation.kind != KIND_CHAT or conversation.title_custom or conversation.title:
        return
    conversation.title = ' '.join((text or '').split())[:AUTO_TITLE_CHARS] or '图片消息'


def sync_care_title(conversation, topic):
    """系统会话的标题跟随主题标题，主人改过名则不动。"""
    if not conversation.title_custom and conversation.title != topic.title:
        conversation.title = (topic.title or '')[:80]


def rename(conversation, title, now):
    title = ' '.join(title.split()) if isinstance(title, str) else ''
    if not 1 <= len(title) <= MAX_CUSTOM_TITLE:
        raise ConversationError(400, '标题需为 1 到 %d 个字' % MAX_CUSTOM_TITLE)
    conversation.title, conversation.title_custom, conversation.updated_at = title, 1, now


def touch(conversation, at):
    """会话里落了一条回合：更新最后活动时间（列表按它排序）。"""
    if conversation.last_turn_at is None or conversation.last_turn_at < at:
        conversation.last_turn_at = at
    conversation.updated_at = at


# ── 历史与列表 ────────────────────────────────────────────────────────────

def history(session, pet_id, before_turn_id, conversation_id, limit=12):
    """当前会话内、该回合之前、已完成的回合，按时间正序。模型历史与路由器共用。"""
    rows = session.execute(select(AgentTurn).where(
        AgentTurn.pet_id == pet_id, AgentTurn.conversation_id == conversation_id,
        AgentTurn.id < before_turn_id, AgentTurn.status == 'complete',
    ).order_by(AgentTurn.id.desc()).limit(limit)).scalars().all()
    return list(reversed(rows))


def _stats(session, ids):
    """每个会话的 (回合数, 未读数)。"""
    if not ids:
        return {}
    rows = session.execute(select(
        AgentTurn.conversation_id, func.count(AgentTurn.id),
        func.sum(case((AgentTurn.read_at.is_(None) & (AgentTurn.status != 'pending'), 1), else_=0)),
    ).where(AgentTurn.conversation_id.in_(ids)).group_by(AgentTurn.conversation_id)).all()
    return {conversation_id: (count, unread or 0) for conversation_id, count, unread in rows}


def _last_turns(session, ids):
    """每个会话的最后一条回合（预览用）。"""
    if not ids:
        return {}
    last_ids = session.execute(select(func.max(AgentTurn.id)).where(
        AgentTurn.conversation_id.in_(ids)).group_by(AgentTurn.conversation_id)).scalars().all()
    turns = session.execute(select(AgentTurn).where(AgentTurn.id.in_(last_ids))).scalars().all()
    return {turn.conversation_id: turn for turn in turns}


def preview_text(turn):
    """预览取主人原话，没有则取首段正文，压成一行并截断。"""
    if turn is None:
        return ''
    text = (turn.text or '').strip() or (turn.reply or '').strip()
    return ' '.join(text.split())[:PREVIEW_CHARS]


def summary(session, row, stats=None, last=None):
    """会话摘要：列表项、各种接口的返回与 WS 的 conversation 帧共用这一份。"""
    count, unread = (stats or {}).get(row.id, (0, 0))
    turn = (last or {}).get(row.id)
    return {
        'id': row.id, 'petId': row.pet_id, 'kind': row.kind, 'careKind': row.care_kind,
        'title': row.title, 'titleCustom': bool(row.title_custom),
        'createdAt': iso_z(row.created_at), 'updatedAt': iso_z(row.updated_at),
        'lastTurnAt': iso_z(row.last_turn_at) if row.last_turn_at else None,
        'turnCount': count, 'unread': unread,
        'preview': {'text': preview_text(turn), 'at': iso_z(turn.created_at)} if turn else None,
        'deletedAt': iso_z(row.deleted_at) if row.deleted_at else None,
    }


def public(session, row):
    return summary(session, row, _stats(session, [row.id]), _last_turns(session, [row.id]))


def listing(session, pet, limit=LIST_PAGE, offset=0):
    """会话列表（不含已删除），按最近活动倒序；没有回合的会话按创建时间排。返回 (列表, 总数)。"""
    condition = (AgentConversation.pet_id == pet.id, AgentConversation.deleted_at.is_(None))
    total = session.execute(select(func.count()).select_from(
        select(AgentConversation.id).where(*condition).subquery())).scalar_one()
    rows = session.execute(select(AgentConversation).where(*condition).order_by(
        func.coalesce(AgentConversation.last_turn_at, AgentConversation.created_at).desc(),
        AgentConversation.id.desc()).limit(limit).offset(offset)).scalars().all()
    ids = [row.id for row in rows]
    stats, last = _stats(session, ids), _last_turns(session, ids)
    return [summary(session, row, stats, last) for row in rows], total


def mark_read(session, conversation, ids=None, now=None):
    """标已读：不带 ids 标该会话全部未读，带 ids 只标这些。返回被标的回合（按 id 倒序）。"""
    now = now or utc_now()
    stmt = select(AgentTurn).where(AgentTurn.conversation_id == conversation.id,
                                   AgentTurn.read_at.is_(None), AgentTurn.status != 'pending')
    if ids is not None:
        stmt = stmt.where(AgentTurn.id.in_(ids))
    rows = session.execute(stmt.order_by(AgentTurn.id.desc())).scalars().all()
    for row in rows:
        row.read_at = now
    return rows


# ── 删除 ──────────────────────────────────────────────────────────────────

def busy(session, conversation):
    """删除前检查：进行中的委托数与未结束的关怀主题数。"""
    tasks = session.execute(select(func.count()).select_from(AgentTask).where(
        AgentTask.conversation_id == conversation.id,
        AgentTask.status.in_(ACTIVE_TASK_STATUSES))).scalar_one()
    topics = session.execute(select(func.count()).select_from(AgentCareTopic).where(
        AgentCareTopic.conversation_id == conversation.id,
        AgentCareTopic.status.in_(OPEN_TOPIC_STATUSES))).scalar_one()
    return tasks, topics


def delete(session, conversation, now=None):
    tasks, topics = busy(session, conversation)
    if tasks or topics:
        parts = (['%d 个进行中的委托' % tasks] if tasks else []) + \
                (['%d 个未结束的提醒' % topics] if topics else [])
        raise ConversationError(409, '这个会话里还有' + '和'.join(parts) + '，结束后才能删除',
                                code='conversation_busy', activeTasks=tasks, openCareTopics=topics)
    conversation.deleted_at = conversation.updated_at = now or utc_now()


# ── 运行上下文与深链 ──────────────────────────────────────────────────────

def care_hint(session, conversation):
    """关怀会话的确定性提示：让模型知道本会话对应哪条主动提醒，据此直接查 read_care_topics。"""
    topic = session.execute(select(AgentCareTopic).where(
        AgentCareTopic.conversation_id == conversation.id)).scalars().first()
    if topic is None:
        return None
    return '本会话来自主动提醒：主题 #%d「%s」（%s）' % (
        topic.id, topic.title, TOPIC_STATUS_LABELS.get(topic.status, topic.status))


def conversation_visible(session, conversation_id):
    """会话仍然可见（存在且未删除）；行不存在按不可见处理。"""
    row = session.get(AgentConversation, conversation_id) if conversation_id else None
    return row is not None and row.deleted_at is None


def turn_visible(session, turn):
    """回合所在的会话仍然可见。没有会话的回合只在数据异常时出现，按不可见处理。"""
    return turn is not None and conversation_visible(session, turn.conversation_id)


def push_summary(user_id, conversation_id):
    """会话摘要帧：后台交付之后调用（调用方不在事务内），客户端据此更新抽屉列表。"""
    from petsense.agent import ws
    with session_scope() as s:
        row = s.get(AgentConversation, conversation_id) if conversation_id else None
        if row is not None:
            ws.push_conversation(user_id, public(s, row))
