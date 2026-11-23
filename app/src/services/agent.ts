import { absoluteUrl, getToken, request, uploadRequest } from '@/services/api';
import type { UploadImage } from '@/services/normalize-image';

/** W07 聊天图片引用（服务端 public() 的 images 项） */
export type AgentImageMeta = {
  id: string;
  width: number | null;
  height: number | null;
  bytes: number | null;
};

/** W03 确认提案卡（agent-w03-spec.md §3.5）：fields 为归一化候选，状态由决策端点翻转。
 * W08 起 kind=task_change 的 fields 形状见 AgentTaskChangeFields（按 kind 区分，卡片分派渲染）。 */
export type AgentProposal = {
  id: number;
  kind: string;
  status: 'pending' | 'confirmed' | 'rejected' | 'superseded';
  version: number;
  fields: {
    name?: string;
    breed?: string;
    birthDate?: string;
    birthDatePrecision?: 'day' | 'month' | 'year';
    weightKg?: number;
    weightMeasuredOn?: string;
    weightRawText?: string;
  };
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
  topicId: number | null;
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

export type AgentTurn = {
  id: number;
  requestId: string;
  text: string;
  speaker: 'pet' | 'assistant';
  readAt: string | null;
  target: 'auto' | 'pet' | 'assistant';
  reply: string;
  status: 'pending' | 'complete' | 'failed';
  createdAt: string;
  topicId: number | null;
  replyTo: number | null;
  sources: AgentSource[];
  blocks:
    | (
        | { type: 'text'; speaker: 'pet' | 'assistant'; text: string }
        | { type: 'proposal'; proposal: AgentProposal }
        | { type: 'task_result'; task: AgentTaskResultBlock }
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
};
export const fetchAgentTurns = (pet: number, before?: number) =>
  request<{ turns: AgentTurn[] }>(`/agent/pets/${pet}/turns${before ? `?before=${before}` : ''}`);
export const sendAgentTurn = (
  pet: number,
  text: string,
  target: AgentTurn['target'],
  requestId: string,
  topicId?: number,
  replyTo?: number,
  images?: string[],
) =>
  request<{ turn: AgentTurn }>(`/agent/pets/${pet}/turns`, {
    method: 'POST',
    body: { text, target, requestId, topicId, replyTo, ...(images?.length ? { images } : {}) },
  });

/** 确认卡决策：幂等；响应带刷新后的 turn（按 id 覆盖本地回合）。 */
export const decideAgentProposal = (
  pet: number,
  proposalId: number,
  action: 'confirm' | 'reject',
) =>
  request<{ proposal: AgentProposal; turn: AgentTurn | null }>(
    `/agent/pets/${pet}/proposals/${proposalId}/decision`,
    { method: 'POST', body: { action } },
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

export const markAgentRead = (pet: number, ids: number[]) =>
  request<{ turns: AgentTurn[] }>(`/agent/pets/${pet}/read`, { method: 'POST', body: { ids } });
