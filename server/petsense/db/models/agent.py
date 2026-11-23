"""Durable owner turns, model output and run trajectories; request IDs deduplicate retries."""
from sqlalchemy import Column, ForeignKey, Float, Integer, String, Text, UniqueConstraint
from petsense.db.base import Base, UTCDatetime


class AgentTurn(Base):
    __tablename__ = 'agent_turns'
    id = Column(Integer, primary_key=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    request_id = Column(String(80), nullable=False)
    text = Column(Text, nullable=False)
    target = Column(String(16), nullable=False)
    topic_id = Column(Integer, nullable=True)
    reply_to = Column(Integer, nullable=True)
    sources = Column(Text, nullable=False, default='[]')
    speaker = Column(String(16), nullable=False, default='assistant')
    read_at = Column(UTCDatetime, nullable=True)
    reply = Column(Text, nullable=False, default='')
    blocks = Column(Text, nullable=True)
    images = Column(Text, nullable=True)  # W07 owner 附件引用 JSON：[{id,width,height,bytes}]
    status = Column(String(16), nullable=False)
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (UniqueConstraint('pet_id', 'request_id'),)


class AgentRun(Base):
    """W01 运行轨迹：完整判断与工具路径，可回放可审计；不对 App 开放（P00 §3）。

    W02 增加 snapshot：本次运行注入的常驻快照原文（数值溯源取证，agent-w02-spec.md §3.7）。
    """
    __tablename__ = 'agent_runs'
    id = Column(Integer, primary_key=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    turn_id = Column(Integer, nullable=True)
    form = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    conclusion = Column(Text, nullable=False, default='{}')
    messages = Column(Text, nullable=False, default='[]')
    tool_summary = Column(Text, nullable=False, default='[]')
    budget = Column(Text, nullable=False, default='{}')
    snapshot = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)


class AgentLearningDay(Base):
    __tablename__ = 'agent_learning_days'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    device_id = Column(String(64), primary_key=True)
    feature = Column(String(16), primary_key=True)
    version = Column(String(80), primary_key=True)
    day = Column(String(10), primary_key=True)
    seconds = Column(Float, nullable=False, default=0)
    last_seen_at = Column(UTCDatetime, nullable=False)
    slots = Column(Text, nullable=False, default='[]')


class PetBaseline(Base):
    """W05 个人基线物化行（agent-w05-spec.md §3.2）：一行一个 (宠物, 维度, 模型版本)。

    证据仍在 agent_learning_days 与分钟明细，本行是可复算的确定性快照；
    重算原地更新并 revision+1，比较结果引用 version+revision+窗口即可溯源。
    """
    __tablename__ = 'pet_baselines'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    feature = Column(String(16), primary_key=True)
    version = Column(String(80), primary_key=True)
    state = Column(String(16), nullable=False, default='learning')
    valid_days = Column(Integer, nullable=False, default=0)
    window_last_day = Column(String(10), nullable=False, default='')
    sample_days = Column(Text, nullable=False, default='[]')
    day_values = Column(Text, nullable=False, default='{}')
    day_stats = Column(Text, nullable=False, default='{}')
    slot_stats = Column(Text, nullable=False, default='[]')
    revision = Column(Integer, nullable=False, default=0)
    computed_at = Column(UTCDatetime, nullable=False)


class CompanionSettings(Base):
    """W03 助手设定：只影响表达、不改变话题与工具（V11 §13.4）；proactivity 由 W09 消费。

    助手属于主人（一人一助手），行按 user_id 懒创建，无行时按 DEFAULTS 渲染。
    """
    __tablename__ = 'companion_settings'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    assistant_name = Column(String(20), nullable=False, default='照料助手')
    owner_title = Column(String(20), nullable=True)
    verbosity = Column(String(8), nullable=False, default='concise')
    proactivity = Column(Integer, nullable=False, default=1)
    updated_at = Column(UTCDatetime, nullable=False)


class AgentJob(Base):
    """通用持久作业行（W08，agent-w08-spec.md §3；W09 事件运行复用）。

    SQLite 队列：dedup_key 为稳定结果键（唯一），status 覆盖 pending/working/done/dead；
    lease_until 为领取租约，启动恢复把遗留 working 回置 pending。payload 由 kind 对应
    执行器解释；执行器经 agent/jobs.py 注册，不在本层分支。
    """
    __tablename__ = 'agent_jobs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(32), nullable=False)
    dedup_key = Column(String(120), nullable=False, unique=True)
    payload_json = Column(Text, nullable=False, default='{}')
    status = Column(String(16), nullable=False, default='pending')
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    next_run_at = Column(UTCDatetime, nullable=False)
    lease_until = Column(UTCDatetime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    updated_at = Column(UTCDatetime, nullable=False)


class AgentProposal(Base):
    """W03 确认提案：确认门服务的持久对象（agent-w03-spec.md §3.3），W08 计划/W10 设备控制复用。

    status: pending / confirmed / rejected / superseded；同宠物同 kind 的新 pending 提案
    自动把旧 pending 置 superseded（superseded_by 指向新提案）。payload 为归一化候选字段 JSON。
    """
    __tablename__ = 'agent_proposals'
    id = Column(Integer, primary_key=True, autoincrement=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    turn_id = Column(Integer, nullable=True)
    kind = Column(String(24), nullable=False)
    payload = Column(Text, nullable=False, default='{}')
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default='pending')
    superseded_by = Column(Integer, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    decided_at = Column(UTCDatetime, nullable=True)
