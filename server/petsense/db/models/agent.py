"""Durable owner turns, model output and run trajectories; request IDs deduplicate retries."""
from sqlalchemy import Column, ForeignKey, Float, Index, Integer, String, Text, UniqueConstraint
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
    label_epoch = Column(Integer, nullable=True)  # 读法纪元；NULL=修复前旧缓存，须重算
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


class AgentAction(Base):
    """W10 动作执行回执（agent-w10-spec.md §2）：稳定动作 ID + 结构化结果。

    行只在真实执行时创建（提案确认后 / 明确直写草稿），提案候选本身不建行；
    kind: record_audio / diary / social_draft。status: executing / completed /
    failed / unknown（外部提交结果不明确）/ dismissed（仅草稿，用户在发布页移除）。
    request_token 为录音关联凭据（高熵、唯一），设备按命令回传同一值。
    """
    __tablename__ = 'agent_actions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    kind = Column(String(24), nullable=False)
    proposal_id = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False)
    device_id = Column(String(64), nullable=True)
    request_token = Column(String(64), nullable=True, unique=True)
    intent_key = Column(String(64), nullable=True, unique=True)
    duration_sec = Column(Integer, nullable=True)
    input_json = Column(Text, nullable=False, default='{}')
    result_json = Column(Text, nullable=False, default='{}')
    error = Column(Text, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    updated_at = Column(UTCDatetime, nullable=False)
    finished_at = Column(UTCDatetime, nullable=True)
    __table_args__ = (Index('ix_agent_actions_pet_kind_id', 'pet_id', 'kind', 'id'),)


class AgentTask(Base):
    """W08 委托（agent-w08-spec.md §3）：稳定委托标识（pet 内唯一 task_key）+ 追加式计划版本。

    status: waiting_baseline / observing / paused / completed / cancelled；
    「待确认」是 AgentProposal(pending) 态，不落本表（未确认提案不创建调度任务）。
    deadline_at 不因暂停延长；paused_intervals 记录暂停窗口（评估时计为未观察缺口）；
    effective_from = max(计划开始, 确认时间)：确认晚于原定开始时不把先前窗口算作已观察；
    observing_since 为 wait_baseline 委托真正进入观察（基线就绪）的时刻。
    watermark_at 为观测水位（数据位置），last_check_at 为上次模型检查（普通节流 ≥1h）。
    """
    __tablename__ = 'agent_tasks'
    id = Column(Integer, primary_key=True, autoincrement=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    task_key = Column(String(40), nullable=False)
    current_version = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default='observing')
    timezone = Column(String(40), nullable=False, default='Asia/Shanghai')
    deadline_at = Column(UTCDatetime, nullable=False)
    effective_from = Column(UTCDatetime, nullable=False)
    observing_since = Column(UTCDatetime, nullable=True)
    paused_intervals = Column(Text, nullable=False, default='[]')
    topic_id = Column(Integer, nullable=True)
    proposal_id = Column(Integer, nullable=True)
    last_check_at = Column(UTCDatetime, nullable=True)
    watermark_at = Column(UTCDatetime, nullable=True)
    due_attempts = Column(Integer, nullable=False, default=0)
    ended_at = Column(UTCDatetime, nullable=True)
    end_reason = Column(String(24), nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    updated_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (UniqueConstraint('pet_id', 'task_key'),)


class AgentTaskPlan(Base):
    """W08 计划版本（追加式）：version 从 1 递增，payload 为归一化计划 JSON；
    修改/续期追加新版本，历史计划与回信保留可查。"""
    __tablename__ = 'agent_task_plans'
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey('agent_tasks.id'), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)
    proposal_id = Column(Integer, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (UniqueConstraint('task_id', 'version'),)


class AgentTaskDelivery(Base):
    """W08 交付（提醒/回信）：instance_key 为稳定结果键（task 内唯一）。

    提醒按委托及条件实例去重（remind:<上海日期>），回信按委托版本去重（letter:v<版本>）；
    turn_id 关联交付回合（深链原会话），payload 持内容与证据摘要。
    """
    __tablename__ = 'agent_task_deliveries'
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey('agent_tasks.id'), nullable=False, index=True)
    plan_version = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)
    instance_key = Column(String(64), nullable=False)
    turn_id = Column(Integer, nullable=True)
    run_id = Column(Integer, nullable=True)
    archived_memory_id = Column(Integer, nullable=True)
    payload = Column(Text, nullable=False, default='{}')
    created_at = Column(UTCDatetime, nullable=False)
    __table_args__ = (UniqueConstraint('task_id', 'instance_key'),)


class AgentCareTopic(Base):
    """W09 主动关怀主题：设备事实、观测发现与学习完成按实例持久化（agent-w09-spec.md §2/§3）。

    (pet, kind, category, anchor, instance) 唯一：anchor 为归一化事实锚点（设备规则=设备号，
    学习完成=维度:版本，回顾发现=模型给出并经服务端归一），instance 在恢复/关闭后递增，
    新实例须经过前一次恢复或可审计关闭（closed_reason）。相同事件实例只产生一条首发消息，
    恢复只更新本行。evidence 为有界依据列表 JSON（帧时间、数值、来源窗口）。
    """
    __tablename__ = 'agent_care_topics'
    id = Column(Integer, primary_key=True, autoincrement=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    kind = Column(String(16), nullable=False)            # device / observation / learning
    category = Column(String(32), nullable=False)       # battery_low / data_stale / readings_invalid / learning_ready / 回顾发现类别
    anchor = Column(String(160), nullable=False)
    instance = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default='open')   # open / ongoing / recovered / closed
    severity = Column(String(16), nullable=False, default='warning')
    title = Column(String(80), nullable=False, default='')
    detail = Column(Text, nullable=False, default='')
    evidence = Column(Text, nullable=False, default='[]')
    delivered = Column(Integer, nullable=False, default=0)  # 已交付首发/升级消息的回合数
    run_id = Column(Integer, nullable=True)
    opened_at = Column(UTCDatetime, nullable=False)
    updated_at = Column(UTCDatetime, nullable=False)
    recovered_at = Column(UTCDatetime, nullable=True)
    closed_reason = Column(String(120), nullable=True)
    __table_args__ = (UniqueConstraint('pet_id', 'kind', 'category', 'anchor', 'instance'),)


class CareDeviceState(Base):
    """W09 硬件规则检测水位（agent-w09-spec.md §3）：一台设备一行，随合格帧推进。

    语义沿用客户端配置（3 帧开启/3 帧回稳/90 帧无效≈3 分钟/失联 30 秒），帧去重由
    ingest 侧序号门保证——只推进不同帧；缺字段不计作明确无效。owner 变化即换绑，
    换绑时打开的主题按 closed_reason='rebind' 可审计关闭，不编造恢复。
    """
    __tablename__ = 'care_device_states'
    device_id = Column(String(64), primary_key=True)
    owner_id = Column(Integer, nullable=False)
    battery_violation = Column(Integer, nullable=False, default=0)
    battery_recovery = Column(Integer, nullable=False, default=0)
    battery_first_at = Column(UTCDatetime, nullable=True)
    battery_critical = Column(Integer, nullable=False, default=0)  # 本实例是否已升级/已发升级消息
    invalid_streak = Column(Integer, nullable=False, default=0)
    invalid_first_at = Column(UTCDatetime, nullable=True)
    last_frame_at = Column(UTCDatetime, nullable=True)
    last_battery_percent = Column(Float, nullable=True)
    frames_seen = Column(Integer, nullable=False, default=0)
    updated_at = Column(UTCDatetime, nullable=False)


class CarePetState(Base):
    """W09 宠物级水位（agent-w09-spec.md §2）：回顾首次启用水位与学习状态水位。

    enabled_at 为该宠首次被调度扫描看到的时间：早于它的调度点不补跑（D4 首次启用
    从下一调度点开始）。learning_watermark 记录上次观察到的 (维度:版本→状态)，首次
    观察只初始化不产生事件（生产首启不把历史已就绪当新事件）。
    """
    __tablename__ = 'care_pet_states'
    pet_id = Column(Integer, ForeignKey('pets.id'), primary_key=True)
    enabled_at = Column(UTCDatetime, nullable=False)
    learning_watermark = Column(Text, nullable=False, default='{}')
    updated_at = Column(UTCDatetime, nullable=False)
