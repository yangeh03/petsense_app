"""账号域模型。"""
from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, text

from petsense.db.base import Base, UTCDatetime


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(40), nullable=False)
    display_name = Column(String(40), nullable=False)
    password_hash = Column(String(128), nullable=False)
    salt = Column(String(32), nullable=False)
    avatar_emoji = Column(String(16), nullable=False, default='🙂')
    bio = Column(Text, nullable=False, default='')
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        # 大小写不敏感唯一：SQLite/PG 双方言等价（替代 SQLite-only 的 COLLATE NOCASE）
        Index('uq_users_username_lower', text('(lower(username))'), unique=True),
    )


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    expires_at = Column(UTCDatetime, nullable=False)
