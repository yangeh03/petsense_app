"""宠物域模型。"""
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Text

from petsense.db.base import Base, UTCDatetime


class Pet(Base):
    __tablename__ = 'pets'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String(40), nullable=False)
    species = Column(String(16), nullable=False, default='dog')
    breed = Column(String(80), nullable=False, default='')
    avatar_emoji = Column(String(16), nullable=False, default='🐾')
    device_id = Column(String(64), nullable=False, default='', index=True)
    petgif_id = Column(String(64), nullable=True)
    avatar_image = Column(String(128), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)


class DailyStat(Base):
    __tablename__ = 'daily_stats'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    date = Column(Date, primary_key=True)
    sleep_seconds = Column(Integer, nullable=False, default=0)
    active_seconds = Column(Integer, nullable=False, default=0)
    hr_sum = Column(Float, nullable=False, default=0)
    hr_count = Column(Integer, nullable=False, default=0)
    samples = Column(Integer, nullable=False, default=0)


class HomeLayout(Base):
    __tablename__ = 'home_layout'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    rooms_json = Column(Text, nullable=False)
    safe_zone_json = Column(Text, nullable=False)
    anchors_json = Column(Text, nullable=False, default='[]')
    updated_at = Column(UTCDatetime, nullable=False)
