"""遥测域模型：环冲原始帧 + 动作识别预测。"""
from sqlalchemy import BigInteger, Column, Float, Index, Integer, String, Text, text

from petsense.db.base import Base, UTCDatetime


class Reading(Base):
    __tablename__ = 'readings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String(255), nullable=False)
    payload = Column(Text, nullable=False)
    received_at = Column(UTCDatetime, nullable=False)
    device_id = Column(String(64), nullable=True)
    __table_args__ = (
        Index('ix_readings_topic_received', 'topic', 'received_at'),
        Index('ix_readings_device_received', 'device_id', 'received_at'),
    )


class ActionPrediction(Base):
    __tablename__ = 'action_predictions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), nullable=False)
    event_id = Column(String(80), nullable=False)
    label = Column(String(64), nullable=False)
    action_key = Column(String(64), nullable=False)
    confidence = Column(Float, nullable=False)
    scores_json = Column(Text, nullable=False)
    sample_count = Column(Integer, nullable=False)
    window_start_ts_ms = Column(BigInteger, nullable=False)
    window_end_ts_ms = Column(BigInteger, nullable=False)
    model_version = Column(String(64), nullable=False)
    predicted_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index('action_predictions_device_time', 'device_id', text('id DESC')),
    )


class TelemetryLatest(Base):
    __tablename__ = 'telemetry_latest'
    owner_id = Column(Integer, nullable=False)
    device_id = Column(String(64), primary_key=True)
    boot_id = Column(String(80), nullable=False)
    ordinal = Column(BigInteger, nullable=False)
    retired_boots = Column(Text, nullable=False, default='[]')
    payload = Column(Text, nullable=False)
    received_at = Column(UTCDatetime, nullable=False)


class TelemetryMinute(Base):
    __tablename__ = 'telemetry_minutes'
    owner_id = Column(Integer, primary_key=True)
    device_id = Column(String(64), primary_key=True)
    minute = Column(UTCDatetime, primary_key=True)
    metrics = Column(Text, nullable=False, default='{}')
    actions = Column(Text, nullable=False, default='{}')
    observed_seconds = Column(Float, nullable=False, default=0)
    frames = Column(Integer, nullable=False, default=0)


class TelemetryEvent(Base):
    __tablename__ = 'telemetry_events'
    owner_id = Column(Integer, nullable=False)
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), nullable=False)
    boot_id = Column(String(80), nullable=False)
    model_version = Column(String(80), nullable=False)
    label = Column(String(32), nullable=False)
    started_at = Column(UTCDatetime, nullable=False)
    last_seen_at = Column(UTCDatetime, nullable=False)
    frames = Column(Integer, nullable=False)
    confidence_sum = Column(Float, nullable=False)
    observed_seconds = Column(Float, nullable=False, default=0)
    experimental = Column(Integer, nullable=False, default=0)
    __table_args__ = (Index('ix_telemetry_events_device_time', 'device_id', 'last_seen_at'),)
