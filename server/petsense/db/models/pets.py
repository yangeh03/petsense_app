"""宠物域模型。"""
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint

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
    avatar_template_id = Column(String(32), nullable=True)
    avatar_image = Column(String(128), nullable=True)
    # W03 档案扩展（agent-w03-spec.md §3.2）：生日与精度；year 存 YYYY-01-01、month 存 YYYY-MM-01
    birth_date = Column(Date, nullable=True)
    birth_date_precision = Column(String(8), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)


class PetWeightRecord(Base):
    """W03 带日期体重记录（按日追加，同日再记覆盖当日；唯一真值在 pets 域）。"""
    __tablename__ = 'pet_weight_records'
    id = Column(Integer, primary_key=True, autoincrement=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    measured_on = Column(Date, nullable=False)
    weight_g = Column(Integer, nullable=False)
    source = Column(String(16), nullable=False, default='owner')
    raw_text = Column(String(40), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (UniqueConstraint('pet_id', 'measured_on'),)


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
