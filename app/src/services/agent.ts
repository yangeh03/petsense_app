import { absoluteUrl, getToken, request, uploadRequest } from '@/services/api';
import type { AgentChangeEvidence } from '@/services/agent-change';
import type { UploadImage } from '@/services/normalize-image';

/** W07 聊天图片引用（服务端 public() 的 images 项） */
export type AgentImageMeta = {
  id: string;
  width: number | null;
  height: number | null;
  bytes: number | null;
};

/** W03 确认提案卡（agent-w03-spec.md §3.5）：fields 为归一化候选，状态由决策端点翻转。
 * W10 起 kind 扩展 record_audio / create_diary / social_draft，候选字段随 kind 不同；
 * W08 起 kind=task_change 的 fields 形状见 AgentTaskChangeFields（按 kind 区分，卡片分派渲染）。 */
export type AgentProposalFields = {
  name?: string;
  breed?: string;
  birthDate?: string;
  birthDatePrecision?: 'day' | 'month' | 'year';
  weightKg?: number;
  weightMeasuredOn?: string;
  weightRawText?: string;
  durationSec?: number;
  deviceId?: string;
  deviceName?: string;
  date?: string;
  replaceJobId?: string | null;
  walkingSeconds?: number;
  observedSeconds?: number;
  steps?: number;
  caption?: string;
  location?: string | null;
};

export type AgentProposal = {
  id: number;
  kind: string;
  status: 'pending' | 'confirmed' | 'rejected' | 'superseded';
  version: number;
  fields: AgentProposalFields;
  createdAt: string;
  decidedAt: string | null;
};

/** W08 委托计划（服务端 tasks.normalize_plan 的归一化产物） */
export type AgentTaskCondition = {
  metric: 'walkSeconds' | 'soundEventCount';
  op: 'at_least' | 'at_most';
  value: number;
  scope: 'window' | 'daily';
};

export type AgentTaskPlan = {
  kind: 'fact_watch' | 'comparison' | 'scheduled_review';
  title: string;
  question: string;
  feature: 'actions' | 'sounds' | null;
  condition: AgentTaskCondition | null;
  comparison: { band: 'below' | 'above' } | null;
  baselineStrategy: 'fact_first' | 'wait_baseline' | null;
  /** W11 D3：基线未就绪时确认卡须由主人明确选择（缺少选择不静默生效）。 */
  baselineStrategyChoiceRequired?: boolean;
  timezone: string;
  startAt: string;
  deadlineAt: string;
};

/** kind=task_change 提案的 fields（proposal.fields 的 W08 形状） */
export type AgentTaskChangeFields = {
  action: 'create' | 'update' | 'renew' | 'pause' | 'resume' | 'cancel';
  plan?: AgentTaskPlan;
  taskId?: number;
  expectedVersion?: number;
  note?: string;
};

export type AgentTaskStatus =
  'waiting_baseline' | 'observing' | 'paused' | 'completed' | 'cancelled';

export type AgentTaskDelivery = {
  id: number;
  taskId: number;
  kind: 'reminder' | 'letter' | 'degraded_letter';
  /** W11 D6：交付时冻结的个人比较证据（历史回信按原证据展示）。 */
  changeEvidence?: AgentChangeEvidence[];
  planVersion: number;
  text: string;
  windowStart: string | null;
  windowEnd: string | null;
  observedMinutes: number;
  gapMinutes: number;
  pausedMinutes: number;
  walkSeconds: number | null;
  soundEventCount: number | null;
  condition: { met?: boolean; [key: string]: unknown } | null;
  degraded: boolean;
  turnId: number | null;
  createdAt: string;
};

export type AgentTask = {
  id: number;
  taskKey: string;
  status: AgentTaskStatus;
  statusLabel: string;
  kind: AgentTaskPlan['kind'];
  kindLabel: string;
  title: string;
  question: string;
  feature: AgentTaskPlan['feature'];
  featureLabel: string | null;
  condition: AgentTaskCondition | null;
  comparison: AgentTaskPlan['comparison'];
  baselineStrategy: AgentTaskPlan['baselineStrategy'];
  timezone: string;
  startAt: string;
  deadlineAt: string;
  effectiveFrom: string;
  currentVersion: number;
  /** W16：确认该委托的会话（提醒与回信都落在这里）；没有会话只可能是数据异常 */
  conversationId: number | null;
  /** 会话已被主人删除：委托簿不再提供「回到原会话」 */
  conversationDeleted: boolean;
  pausedIntervals: { start: string; end: string | null }[];
  reminders: number;
  letters: number;
  lastDeliveryAt: string | null;
  createdAt: string;
  updatedAt: string;
  endedAt: string | null;
  endReason: string | null;
};

export type AgentTaskDetail = AgentTask & {
  deliveries: AgentTaskDelivery[];
  versions: { version: number; plan: AgentTaskPlan; createdAt: string }[];
};

export type AgentWindowSource = {
  type: 'observations' | 'current_status' | 'baseline';
  startAt: string | null;
  endAt: string | null;
  observedSeconds?: number | null;
  soundEventCount?: number | null;
};

/** 网页来源（W06 服务端 sources 并入）：title/checkedAt 由服务端写入，未知 type 由 UI 安全降级。 */
export type AgentWebSource = { type: 'web'; url: string; title: string; checkedAt: string };

export type AgentSource = AgentWindowSource | AgentWebSource;

export const isWebSource = (source: AgentSource): source is AgentWebSource => source.type === 'web';

/** W10 动作回执（agent-w10-spec.md §2）：录音/日记/草稿的执行状态；text 为旧客户端可读降级。 */
export type AgentAction = {
  id: number;
  kind: 'record_audio' | 'create_diary' | 'social_draft' | string;
  status: 'executing' | 'completed' | 'failed' | 'unknown' | 'dismissed' | string;
  createdAt: string;
  updatedAt: string;
  text: string;
  error?: string | null;
  durationSec?: number | null;
  deviceId?: string | null;
  deviceName?: string | null;
  commandSent?: boolean | null;
  audioReceived?: boolean | null;
  audio?: {
    durationMs: number;
    sampleRate: number;
    bits: number;
    channels: number;
    bytes: number;
  } | null;
  audioUrl?: string;
  job?: {
    jobId: string;
    status: string;
    posterUrl: string | null;
    message: string | null;
    date?: string | null;
  } | null;
  draft?: { caption: string; location: string | null } | null;
};

/** W09 主动关怀块（care.py / care_jobs.py 写入）：设备事件、周期回顾与学习完成提醒。 */
export type AgentCareInfo = {
  kind: 'review' | 'device' | 'learning' | string;
  category: string;
  title: string;
  status: string;
  severity?: string;
  topicId?: number | null;
  openedAt?: string | null;
  windowStart?: string;
  windowEnd?: string;
  evidence?: {
    at: string;
    kind: string;
    battery?: number | null;
    healthValid?: boolean | null;
    note?: string;
  }[];
  runId?: number | null;
};

/** W09 主题（GET /care/topics）：稳定 ID、状态、依据与时间，供追溯与下钻。 */
export type AgentCareTopic = {
  id: number;
  kind: string;
  category: string;
  anchor: string;
  instance: number;
  status: 'open' | 'ongoing' | 'recovered' | 'closed' | string;
  severity: string;
  title: string;
  detail: string;
  evidence: AgentCareInfo['evidence'];
  delivered: number;
  openedAt: string;
  updatedAt: string;
  recoveredAt: string | null;
  closedReason: string | null;
};

/**
 * 双 Agent 群聊协议（docs/agent-dual-agent-chat-design.md §4）。客户端在请求上声明它，是否生效由
 * 服务端按开关与账号灰度决定：生效时回合带 messages（逐条回复）、POST 立即返回 202；未生效时
 * 响应与旧协议完全一致，客户端按旧形状渲染。
 */
export const AGENT_GROUP_PROTOCOL = 'group-v1';

export type AgentReplyMessageStatus = 'queued' | 'running' | 'complete' | 'failed';

/**
 * 群聊协议下的一条回复：爱宠或照料助手在一轮里的一条发言。id 自创建起稳定（m<行 id>），
 * 同一条发言从出字到结束、失败后重试都是同一个 id；旧回合按内容块投影成只读消息（t<回合 id>-<序号>）。
 */
export type AgentReplyMessage = {
  id: string;
  turnId: number;
  role: 'pet' | 'assistant';
  position: number;
  kind: string;
  status: AgentReplyMessageStatus;
  /** 累计正文；失败的发言为空串（已流出的半截由更高版本的快照撤回） */
  text: string;
  /** 每次持久化变更递增：客户端只接受不低于已有版本的消息，同版本只接受不更短的正文 */
  version: number;
  sources: AgentSource[];
  blocks: AgentTurn['blocks'];
  error: string | null;
  errorNote: string | null;
  readOnly: boolean;
  createdAt: string;
  finishedAt: string | null;
  /** 仅客户端使用的瞬时进度（如联网核实中）：不是正文，不落库，出字后不再显示 */
  activity?: 'searching' | null;
};

export type AgentTurn = {
  id: number;
  requestId: string;
  text: string;
  speaker: 'pet' | 'assistant';
  readAt: string | null;
  /** both（两位都回）是群聊协议新增的取值；未开放双 Agent 时服务端不接受 */
  target: 'auto' | 'pet' | 'assistant' | 'both';
  reply: string;
  status: 'pending' | 'complete' | 'failed';
  createdAt: string;
  /** W16：所属会话；帧与回合都按它归位 */
  conversationId: number | null;
  replyTo: number | null;
  /** 消息级引用（群聊协议）：精确到被引用回合里的某一条回复 */
  replyToMessage?: string | null;
  sources: AgentSource[];
  /** 仅群聊协议的回合带（可以是空数组：还没路由出谁来说话）；旧协议的回合没有这个字段 */
  messages?: AgentReplyMessage[];
  blocks:
    | (
        | { type: 'text'; speaker: 'pet' | 'assistant'; text: string }
        | { type: 'proposal'; proposal: AgentProposal }
        | { type: 'action'; action: AgentAction }
        | { type: 'task_result'; task: AgentTaskResultBlock }
        | { type: 'care'; care: AgentCareInfo }
        | { type: 'personal_change'; change: AgentChangeEvidence }
      )[]
    | null;
  images: AgentImageMeta[];
};

/** W08 后台交付块（提醒/回信）：text 块承载正文（旧客户端可读降级），本块供卡片与下钻 */
export type AgentTaskResultBlock = {
  taskId: number;
  deliveryId: number;
  kind: 'reminder' | 'letter' | 'degraded_letter';
  title: string | null;
  planKind: AgentTaskPlan['kind'];
  version: number;
  deadlineAt: string;
  degraded: boolean;
  /** W11 D6：回信冻结的个人比较证据。 */
  changeEvidence?: AgentChangeEvidence[];
};
/** W16 会话摘要（docs/agent-w16-spec.md §5.2）：列表项、接口返回与 WS 的 conversation 帧共用这一份 */
export type AgentConversation = {
  id: number;
  petId: number;
  /** chat 是主人的会话；care 是主动消息自带的系统会话 */
  kind: 'chat' | 'care';
  /** 系统会话的细分：设备提醒 / 学习完成 / 周期回顾 / 委托兜底 */
  careKind: 'device' | 'learning' | 'review' | 'task' | null;
  /** 空会话的标题是空串，界面显示「新对话」 */
  title: string;
  titleCustom: boolean;
  createdAt: string;
  updatedAt: string;
  /** 最后一条回合的时间；没有回合的会话为空，列表按它（或创建时间）倒序 */
  lastTurnAt: string | null;
  turnCount: number;
  /** 会话内未读的回合数 */
  unread: number;
  preview: { text: string; at: string } | null;
  /** 非空表示已删除：只有 WS 的 conversation 帧会带出已删除的会话，接口列表里不会有 */
  deletedAt: string | null;
};

/** 某个会话的历史列表；conversation 是它的摘要，dualAgent / protocol 只在服务端确认当前账号进入双 Agent 时出现。 */
export type AgentTurnListing = {
  turns: AgentTurn[];
  conversation?: AgentConversation;
  protocol?: string;
  dualAgent?: boolean;
};

/** 取某个会话的历史（每页 40 条，before 为上一页最早一条的 id）。 */
export const fetchAgentTurns = (pet: number, conversationId: number, before?: number) =>
  request<AgentTurnListing>(
    `/agent/pets/${pet}/turns?${new URLSearchParams({
      conversationId: String(conversationId),
      ...(before ? { before: String(before) } : {}),
      protocol: AGENT_GROUP_PROTOCOL,
    })}`,
  );

/**
 * 发一条主人消息。群聊协议生效时立即返回 202 与带 messages 的回合（生成在后台，结果走 WS）；
 * 未生效时仍是旧协议的 200。target=both 而账号未开放双 Agent 时返回 400（code=dual_agent_unavailable）。
 * 响应带会话摘要（标题、预览、活动时间随这条消息更新）。
 */
export const sendAgentTurn = (
  pet: number,
  text: string,
  target: AgentTurn['target'],
  requestId: string,
  options: {
    conversationId: number;
    replyTo?: number;
    images?: string[];
    replyToMessage?: string;
  },
) =>
  request<{
    turn: AgentTurn;
    conversation: AgentConversation;
    protocol?: string;
    accepted?: boolean;
  }>(`/agent/pets/${pet}/turns`, {
    method: 'POST',
    body: {
      text,
      target,
      requestId,
      protocol: AGENT_GROUP_PROTOCOL,
      conversationId: options.conversationId,
      ...(options.replyTo ? { replyTo: options.replyTo } : {}),
      ...(options.replyToMessage ? { replyToMessage: options.replyToMessage } : {}),
      ...(options.images?.length ? { images: options.images } : {}),
    },
  });

/**
 * 群聊协议：只重跑这一轮里失败的发言，成功的保留不动；响应是重新进入 pending 的回合。
 * 409：没有可重试的发言（nothing_to_retry）、回合还在进行中（turn_running）、不是群聊回合。
 */
export const retryAgentTurn = (pet: number, turnId: number) =>
  request<AgentTurn>(`/agent/pets/${pet}/turns/${turnId}/retry`, { method: 'POST', body: {} });

/** 确认卡决策：幂等；响应带刷新后的 turn（按 id 覆盖本地回合）。
 *  baselineStrategy 为 W11 D3 的主人实际选择（等基线 / 先记录绝对事实）。
 *  声明群聊协议后，刷新的回合带 messages（卡片状态同步到它所属的那条发言）。 */
export const decideAgentProposal = (
  pet: number,
  proposalId: number,
  action: 'confirm' | 'reject',
  baselineStrategy?: 'fact_first' | 'wait_baseline',
) =>
  request<{ proposal: AgentProposal; turn: AgentTurn | null }>(
    `/agent/pets/${pet}/proposals/${proposalId}/decision`,
    {
      method: 'POST',
      body: {
        action,
        ...(baselineStrategy ? { baselineStrategy } : {}),
        protocol: AGENT_GROUP_PROTOCOL,
      },
    },
  );

/** W08 委托簿：列表 / 详情 / 直接操作（暂停恢复取消即时生效，幂等）。 */
export const fetchAgentTasks = (pet: number) =>
  request<{ tasks: AgentTask[] }>(`/agent/pets/${pet}/tasks`);
export const fetchAgentTask = (pet: number, taskId: number) =>
  request<{ task: AgentTaskDetail }>(`/agent/pets/${pet}/tasks/${taskId}`);
export const agentTaskOp = (pet: number, taskId: number, op: 'pause' | 'resume' | 'cancel') =>
  request<{ task: AgentTask }>(`/agent/pets/${pet}/tasks/${taskId}/ops`, {
    method: 'POST',
    body: { op },
  });

/** W08 确认卡编辑：基于 pending task_change 候选重新提交归一化计划（服务端生成新版本并翻旧卡）。 */
export const submitAgentProposalEdit = (
  pet: number,
  baseProposalId: number,
  plan: Partial<AgentTaskPlan>,
) =>
  request<{ proposal: AgentProposal }>(`/agent/pets/${pet}/task-proposals`, {
    method: 'POST',
    body: { baseProposalId, plan },
  });

/** W07：上传聊天图片（先传后发；返回内容寻址 id 与尺寸）。method 必须显式 POST：Web 路径走 request()，缺省是 GET。 */
export const uploadAgentImage = (pet: number, file: UploadImage) =>
  uploadRequest<AgentImageMeta>(`/agent/pets/${pet}/images`, {
    method: 'POST',
    fields: {},
    files: [{ field: 'image', uri: file.uri, name: file.name, type: file.type }],
  });

/** W07：鉴权图源（仅本人可读，区别于公开 /uploads/） */
export function agentImageSource(imageId: string) {
  const token = getToken();
  return {
    uri: absoluteUrl(`/api/app/agent/images/${imageId}`),
    ...(token ? { headers: { 'X-Petsense-Session': token } } : {}),
  };
}

/** W10：录音鉴权播放地址（仅本人可读；WebView/audio 标签发不了 header，走 ?token=）。 */
export function agentRecordingUrl(actionId: number) {
  const token = getToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : '';
  return absoluteUrl(`/api/app/agent/recordings/${actionId}/audio`) + query;
}

/** W10：本宠私有社区草稿槽（发布页独立加载，与本地手写草稿互不影响）。 */
export type AgentSocialDraft = {
  actionId: number;
  caption: string;
  location: string | null;
  savedAt: string;
};

export const fetchAgentSocialDraft = (pet: number) =>
  request<{ success: boolean; draft: AgentSocialDraft | null }>(`/agent/pets/${pet}/social-draft`);

export const dismissAgentSocialDraft = (pet: number) =>
  request<{ success: boolean; draft: null }>(`/agent/pets/${pet}/social-draft`, {
    method: 'DELETE',
  });

export type AgentObservation = {
  startAt: string;
  endAt: string;
  observedSeconds: number;
  actionSeconds: Record<string, number>;
  actionObservedSeconds: number;
  soundObservedSeconds: number;
  soundEventCount: number;
  metrics: Record<
    string,
    { avg: number | null; min: number | null; max: number | null; count: number }
  >;
  learningDimensions: {
    dimension: string;
    version: string;
    validDays: number;
    observedSeconds: number;
    state: string;
    active: boolean;
  }[];
  learning: { validDays: number; requiredDays: number; state: string };
};
export const fetchAgentObservation = (pet: number, start: string, end: string) =>
  request<AgentObservation>(
    `/agent/pets/${pet}/observations?${new URLSearchParams({ start, end })}`,
  );

/** W16：会话列表（按最近活动倒序，不含已删除）。 */
export const fetchAgentConversations = (
  pet: number,
  options: { limit?: number; offset?: number } = {},
) =>
  request<{ conversations: AgentConversation[]; total: number; hasMore: boolean }>(
    `/agent/pets/${pet}/conversations?${new URLSearchParams({
      ...(options.limit != null ? { limit: String(options.limit) } : {}),
      ...(options.offset ? { offset: String(options.offset) } : {}),
    })}`,
  );

/** W16：新建会话。最近一个仍为空且未改名的主人会话会被复用（created=false）。 */
export const createAgentConversation = (pet: number) =>
  request<{ conversation: AgentConversation; created: boolean }>(
    `/agent/pets/${pet}/conversations`,
    { method: 'POST', body: {} },
  );

/** W16：改名（1 到 40 个字）。改名后标题不再随首条消息或主题标题变化。 */
export const renameAgentConversation = (conversationId: number, title: string) =>
  request<{ conversation: AgentConversation }>(`/agent/conversations/${conversationId}`, {
    method: 'PATCH',
    body: { title },
  });

/** W16：删除（软删除）。会话里还有进行中的委托或未结束的提醒时返回 409（code=conversation_busy）。 */
export const deleteAgentConversation = (conversationId: number) =>
  request<{ success: true }>(`/agent/conversations/${conversationId}`, { method: 'DELETE' });

/**
 * W16：把一个会话标为已读。不带 ids 标全部未读，带 ids 只标这些；返回会话摘要与这次新标已读的回合
 * （最多最近 40 条）。已读过的回合不会再次返回。
 */
export const markConversationRead = (conversationId: number, ids?: number[]) =>
  request<{ conversation: AgentConversation; turns: AgentTurn[]; markedCount: number }>(
    `/agent/conversations/${conversationId}/read`,
    { method: 'POST', body: { ...(ids?.length ? { ids } : {}), protocol: AGENT_GROUP_PROTOCOL } },
  );

/** W09：主题追溯列表（可选状态过滤；依据随行，供「为什么提醒我」下钻）。 */
export const fetchCareTopics = (pet: number, status?: string) =>
  request<{ topics: AgentCareTopic[] }>(
    `/agent/pets/${pet}/care/topics${status ? `?status=${status}` : ''}`,
  );
