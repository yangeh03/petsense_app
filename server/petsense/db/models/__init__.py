"""全部 ORM 模型（导入即注册到 Base.metadata）。"""
from petsense.db.models.auth import AuthSession, User
from petsense.db.models.collection import (
    CollectionFrame,
    CollectionSession,
    CollectionVideo,
    JournalCrc,
)
from petsense.db.models.devices import Device
from petsense.db.models.pets import DailyStat, HomeLayout, Pet, PetWeightRecord
from petsense.db.models.petgif_diary_job import PetgifDiaryJob
from petsense.db.models.petgif_identity import PetgifIdentity
from petsense.db.models.social import (Comment, DiscoveryLocation, DiscoveryQuery, Follow, Post, PostBookmark,
                                       PostLike, PostMedia, SocialNotification, SocialOutbox,
                                       Story, StoryView)
from petsense.db.models.telemetry import ActionPrediction, Reading, TelemetryLatest, TelemetryMinute, TelemetryEvent

__all__ = [
    'User', 'AuthSession', 'Pet', 'DailyStat', 'HomeLayout', 'PetWeightRecord',
    'Post', 'PostLike', 'PostBookmark', 'PostMedia', 'Comment', 'Follow',
    'Story', 'StoryView', 'DiscoveryLocation', 'DiscoveryQuery', 'SocialNotification', 'SocialOutbox',
    'Reading', 'ActionPrediction', 'Device', 'TelemetryLatest', 'TelemetryMinute', 'TelemetryEvent',
    'CollectionSession', 'CollectionFrame', 'CollectionVideo', 'JournalCrc', 'PetgifDiaryJob',
    'PetgifIdentity', 'AgentTurn', 'AgentLearningDay', 'CompanionSettings', 'AgentProposal', 'PetMemory',
]

from petsense.db.models.agent import AgentTurn, AgentLearningDay, AgentProposal, CompanionSettings
from petsense.db.models.memory import PetMemory
