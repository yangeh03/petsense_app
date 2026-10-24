"""社区域模型。"""
from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, text

from petsense.db.base import Base, UTCDatetime


class Post(Base):
    __tablename__ = 'posts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    pet_id = Column(Integer, nullable=True)
    image_path = Column(String(255), nullable=False, default='')
    caption = Column(Text, nullable=False, default='')
    location_text = Column(String(255), nullable=False, default='')
    media_url = Column(String(500), nullable=True)
    media_kind = Column(String(16), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (
        Index('ix_posts_user_id_id', 'user_id', text('id DESC')),
    )


class PostLike(Base):
    __tablename__ = 'post_likes'
    post_id = Column(Integer, ForeignKey('posts.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    created_at = Column(UTCDatetime, nullable=False)


class Comment(Base):
    __tablename__ = 'comments'
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(UTCDatetime, nullable=False)


class Follow(Base):
    __tablename__ = 'follows'
    follower_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    followee_id = Column(Integer, ForeignKey('users.id'), primary_key=True, index=True)
    created_at = Column(UTCDatetime, nullable=False)
