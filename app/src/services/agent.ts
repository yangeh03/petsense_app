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
 * W10 起 kind 扩展 record_audio / create_diary / social_draft，候选字段随 kind 不同。 */
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
        | { type: 'action'; action: AgentAction }
      )[]
    | null;
  images: AgentImageMeta[];
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

export const markAgentRead = (pet: number, ids: number[]) =>
  request<{ turns: AgentTurn[] }>(`/agent/pets/${pet}/read`, { method: 'POST', body: { ids } });
