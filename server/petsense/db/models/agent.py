"""Durable owner turns and model output; request IDs deduplicate retries."""
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
    status = Column(String(16), nullable=False)
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (UniqueConstraint('pet_id', 'request_id'),)


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
