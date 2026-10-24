"""设备域模型：设备归属（一人一设备）。"""
from sqlalchemy import Column, ForeignKey, Integer, String

from petsense.db.base import Base, UTCDatetime


class Device(Base):
    __tablename__ = 'devices'
    device_id = Column(String(64), primary_key=True)
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True, unique=True)
    name = Column(String(40), nullable=False, default='')
    claimed_at = Column(UTCDatetime, nullable=False)
    last_seen_at = Column(UTCDatetime, nullable=True)
