"""社区域模型。"""
from sqlalchemy import Boolean, Column, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

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
    updated_at = Column(UTCDatetime, nullable=True)
    deleted_at = Column(UTCDatetime, nullable=True)
    audience = Column(String(16), nullable=False, default='public')
    version = Column(Integer, nullable=False, default=1)
    client_mutation_id = Column(String(80), nullable=True)
    __table_args__ = (
        Index('ix_posts_user_id_id', 'user_id', text('id DESC')),
        UniqueConstraint('user_id', 'client_mutation_id', name='uq_posts_mutation'),
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
    root_id = Column(Integer, ForeignKey('comments.id'), nullable=True)
    reply_to_comment_id = Column(Integer, ForeignKey('comments.id'), nullable=True)
    deleted_at = Column(UTCDatetime, nullable=True)
    client_mutation_id = Column(String(80), nullable=True)
    __table_args__ = (
        UniqueConstraint('user_id', 'client_mutation_id', name='uq_comments_mutation'),
        Index('ix_comments_post_root_id', 'post_id', 'root_id', 'id'),
    )


class Follow(Base):
    __tablename__ = 'follows'
    follower_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    followee_id = Column(Integer, ForeignKey('users.id'), primary_key=True, index=True)
    created_at = Column(UTCDatetime, nullable=False)


class PostBookmark(Base):
    __tablename__ = 'post_bookmarks'
    post_id = Column(Integer, ForeignKey('posts.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    created_at = Column(UTCDatetime, nullable=False)


class PostMedia(Base):
    __tablename__ = 'post_media'
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    path = Column(String(255), nullable=False)
    kind = Column(String(16), nullable=False, default='image')
    __table_args__ = (UniqueConstraint('post_id', 'position', name='uq_post_media_position'),)


class Story(Base):
    __tablename__ = 'stories'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    image_path = Column(String(255), nullable=False)
    caption = Column(String(300), nullable=False, default='')
    audience = Column(String(16), nullable=False, default='followers')
    created_at = Column(UTCDatetime, nullable=False)
    expires_at = Column(UTCDatetime, nullable=False, index=True)
    deleted_at = Column(UTCDatetime, nullable=True)
    client_mutation_id = Column(String(80), nullable=True)
    __table_args__ = (UniqueConstraint('user_id', 'client_mutation_id', name='uq_stories_mutation'),)


class StoryView(Base):
    __tablename__ = 'story_views'
    story_id = Column(Integer, ForeignKey('stories.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    viewed_at = Column(UTCDatetime, nullable=False)


class DiscoveryLocation(Base):
    __tablename__ = 'discovery_locations'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    latitude_grid = Column(Float, nullable=False)
    longitude_grid = Column(Float, nullable=False)
    accuracy_meters = Column(Float, nullable=False)
    observed_at = Column(UTCDatetime, nullable=False)
    expires_at = Column(UTCDatetime, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)


class DiscoveryQuery(Base):
    __tablename__ = 'discovery_queries'
    id = Column(String(48), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    center_latitude_grid = Column(Float, nullable=False)
    center_longitude_grid = Column(Float, nullable=False)
    radius_km = Column(Integer, nullable=False)
    candidate_ids_json = Column(Text, nullable=False)
    created_at = Column(UTCDatetime, nullable=False)
    expires_at = Column(UTCDatetime, nullable=False)


class SocialNotification(Base):
    __tablename__ = 'social_notifications'
    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    kind = Column(String(24), nullable=False)
    post_id = Column(Integer, nullable=True)
    comment_id = Column(Integer, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    read_at = Column(UTCDatetime, nullable=True)
    dedup_key = Column(String(160), nullable=False, unique=True)


class SocialOutbox(Base):
    __tablename__ = 'social_outbox'
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(160), nullable=False, unique=True)
    kind = Column(String(24), nullable=False)
    payload_json = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default='pending')
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(UTCDatetime, nullable=False)
    lease_until = Column(UTCDatetime, nullable=True)
