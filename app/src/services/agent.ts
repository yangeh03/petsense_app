import { request } from '@/services/api';

/** W03 确认提案卡（agent-w03-spec.md §3.5）：fields 为归一化候选，状态由决策端点翻转。 */
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
  blocks:
    | (
        | { type: 'text'; speaker: 'pet' | 'assistant'; text: string }
        | { type: 'proposal'; proposal: AgentProposal }
      )[]
    | null;
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
) =>
  request<{ turn: AgentTurn }>(`/agent/pets/${pet}/turns`, {
    method: 'POST',
    body: { text, target, requestId, topicId, replyTo },
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
