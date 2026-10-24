"""采集域模型（文档型存储，结构与旧 collection.sqlite3 一致）。"""
from sqlalchemy import BigInteger, Column, Index, Integer, String, Text

from petsense.db.base import Base


class CollectionSession(Base):
    __tablename__ = 'collection_sessions'
    id = Column(String(80), primary_key=True)
    document = Column(Text, nullable=False)


class CollectionFrame(Base):
    __tablename__ = 'collection_frames'
    device = Column(String(80), primary_key=True)
    boot = Column(String(80), primary_key=True)
    seq = Column(Integer, primary_key=True)
    session = Column(String(80), nullable=False)
    mono_us = Column(BigInteger, nullable=False)
    utc_us = Column(BigInteger, nullable=True)
    received_us = Column(BigInteger, nullable=False)
    document = Column(Text, nullable=False)
    __table_args__ = (
        Index('collection_frames_session', 'session', 'boot', 'seq'),
    )


class CollectionVideo(Base):
    __tablename__ = 'collection_videos'
    id = Column(String(80), primary_key=True)
    session = Column(String(80), nullable=False)
    document = Column(Text, nullable=False)
    complete = Column(Integer, nullable=False, default=0)


class JournalCrc(Base):
    __tablename__ = 'journal_crc'
    device = Column(String(80), primary_key=True)
    boot = Column(String(80), primary_key=True)
    seq = Column(Integer, primary_key=True)
    crc = Column(Integer, nullable=False)
