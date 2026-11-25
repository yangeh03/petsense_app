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
from petsense.db.models.push import PushDelivery, PushInstallation, PushPreference
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
    'PetgifIdentity', 'AgentConversation', 'AgentTurn', 'AgentLearningDay', 'CompanionSettings',
    'AgentProposal', 'PetMemory',
    'AgentAction', 'AgentJob', 'AgentTask', 'AgentTaskPlan', 'AgentTaskDelivery',
    'AgentCareTopic', 'CareDeviceState', 'CarePetState',
    'PushPreference', 'PushInstallation', 'PushDelivery',
    'AgentInboxItem', 'AgentInboxState',
]

from petsense.db.models.agent import (AgentAction, AgentCareTopic, AgentConversation, AgentInboxItem,
    AgentInboxState, AgentJob, AgentLearningDay, AgentProposal, AgentTask, AgentTaskDelivery, AgentTaskPlan,
    AgentTurn, CareDeviceState, CarePetState, CompanionSettings)
from petsense.db.models.memory import PetMemory
from petsense.db.models.push import PushDelivery, PushInstallation, PushPreference
