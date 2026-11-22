import { absoluteUrl, getToken, request, uploadRequest } from '@/services/api';
import type { UploadImage } from '@/services/normalize-image';

/** W07 聊天图片引用（服务端 public() 的 images 项） */
export type AgentImageMeta = {
  id: string;
  width: number | null;
  height: number | null;
  bytes: number | null;
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
  sources: {
    type: string;
    startAt: string;
    endAt: string;
    deviceId?: string;
    versions?: string[];
    observedSeconds?: number | null;
    soundEventCount?: number | null;
  }[];
  blocks: { type: 'text'; speaker: 'pet' | 'assistant'; text: string }[] | null;
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
  }[];
  learning: { validDays: number; requiredDays: number; state: string };
};
export const fetchAgentObservation = (pet: number, start: string, end: string) =>
  request<AgentObservation>(
    `/agent/pets/${pet}/observations?${new URLSearchParams({ start, end })}`,
  );

export const markAgentRead = (pet: number, ids: number[]) =>
  request<{ turns: AgentTurn[] }>(`/agent/pets/${pet}/read`, { method: 'POST', body: { ids } });
