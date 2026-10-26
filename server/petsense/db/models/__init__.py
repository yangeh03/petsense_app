"""全部 ORM 模型（导入即注册到 Base.metadata）。"""
from petsense.db.models.auth import AuthSession, User
from petsense.db.models.collection import (
    CollectionFrame,
    CollectionSession,
    CollectionVideo,
    JournalCrc,
)
from petsense.db.models.devices import Device
from petsense.db.models.pets import DailyStat, HomeLayout, Pet
from petsense.db.models.petgif_diary_job import PetgifDiaryJob
from petsense.db.models.social import Comment, Follow, Post, PostLike
from petsense.db.models.telemetry import ActionPrediction, Reading, TelemetryLatest, TelemetryMinute, TelemetryEvent

__all__ = [
    'User', 'AuthSession', 'Pet', 'DailyStat', 'HomeLayout',
    'Post', 'PostLike', 'Comment', 'Follow',
    'Reading', 'ActionPrediction', 'Device', 'TelemetryLatest', 'TelemetryMinute', 'TelemetryEvent',
    'CollectionSession', 'CollectionFrame', 'CollectionVideo', 'JournalCrc', 'PetgifDiaryJob',
]
