"""Durable, account-scoped jobs for asynchronous PetGIF exercise diaries."""
from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from petsense.db.base import Base, UTCDatetime


class PetgifDiaryJob(Base):
    __tablename__ = 'petgif_diary_jobs'
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    petgif_id = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    status = Column(String(16), nullable=False)
    input_json = Column(Text, nullable=False)
    photo_paths_json = Column(Text, nullable=False, default='[]')
    upstream_diary_id = Column(String(128), nullable=True)
    result_json = Column(Text, nullable=False, default='{}')
    error_message = Column(String(240), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    updated_at = Column(UTCDatetime, nullable=False)
    finished_at = Column(UTCDatetime, nullable=True)
    __table_args__ = (
        UniqueConstraint('user_id', 'pet_id', 'idempotency_key', name='uq_petgif_diary_idempotency'),
        Index('ix_petgif_diary_jobs_pet_created', 'pet_id', 'created_at'),
    )
