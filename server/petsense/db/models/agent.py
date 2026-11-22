"""Durable owner turns, model output and run trajectories; request IDs deduplicate retries."""
from sqlalchemy import Column, ForeignKey, Float, Integer, String, Text, UniqueConstraint
from petsense.db.base import Base, UTCDatetime


class AgentTurn(Base):
    __tablename__ = 'agent_turns'
    id = Column(Integer, primary_key=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    request_id = Column(String(80), nullable=False)
    text = Column(Text, nullable=False)
    target = Column(String(16), nullable=False)
    topic_id = Column(Integer, nullable=True)
    reply_to = Column(Integer, nullable=True)
    sources = Column(Text, nullable=False, default='[]')
    speaker = Column(String(16), nullable=False, default='assistant')
    read_at = Column(UTCDatetime, nullable=True)
    reply = Column(Text, nullable=False, default='')
    blocks = Column(Text, nullable=True)
    status = Column(String(16), nullable=False)
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (UniqueConstraint('pet_id', 'request_id'),)


class AgentRun(Base):
    """W01 运行轨迹：完整判断与工具路径，可回放可审计；不对 App 开放（P00 §3）。

    W02 增加 snapshot：本次运行注入的常驻快照原文（数值溯源取证，agent-w02-spec.md §3.7）。
    """
    __tablename__ = 'agent_runs'
    id = Column(Integer, primary_key=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    turn_id = Column(Integer, nullable=True)
    form = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    conclusion = Column(Text, nullable=False, default='{}')
    messages = Column(Text, nullable=False, default='[]')
    tool_summary = Column(Text, nullable=False, default='[]')
    budget = Column(Text, nullable=False, default='{}')
    snapshot = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)


class AgentLearningDay(Base):
    __tablename__ = 'agent_learning_days'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    device_id = Column(String(64), primary_key=True)
    feature = Column(String(16), primary_key=True)
    version = Column(String(80), primary_key=True)
    day = Column(String(10), primary_key=True)
    seconds = Column(Float, nullable=False, default=0)
    last_seen_at = Column(UTCDatetime, nullable=False)
    slots = Column(Text, nullable=False, default='[]')
