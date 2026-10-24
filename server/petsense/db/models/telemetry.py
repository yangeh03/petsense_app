"""遥测域模型：环冲原始帧 + 动作识别预测。"""
from sqlalchemy import BigInteger, Column, Float, Index, Integer, String, Text, text

from petsense.db.base import Base, UTCDatetime


class Reading(Base):
    __tablename__ = 'readings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String(255), nullable=False)
    payload = Column(Text, nullable=False)
    received_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index('ix_readings_topic_received', 'topic', 'received_at'),
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
