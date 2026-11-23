/**
 * W12 收件箱客户端（agent-w12-spec.md §服务端和跨端契约）。
 *
 * 收件箱条目只存稳定来源引用、类型、时间和已读状态；正文与证据仍从原主题/回信读取。
 * 今日铃铛与今日预览共用这里的列表、计数与已读语义；深链入参只有收件箱 ID，
 * 目标由服务端解析（登录后同一逻辑）。
 */

import { request } from '@/services/api';
import type { AgentTurn } from '@/services/agent';

export type InboxSourceKind = 'device_fact' | 'care' | 'reply' | 'learning';

export type InboxItem = {
  id: number;
  petId: number;
  sourceKind: InboxSourceKind;
  eventKey: string;
  title: string;
  summary: string;
  severity: string;
  sourceStatus: string;
  available: boolean;
  turnId: number | null;
  priority: number;
  previewable: boolean;
  published: boolean;
  highRelevance: boolean;
  pushCandidate: boolean;
  status: 'available' | 'unavailable';
  backfilled: boolean;
  readAt: string | null;
  createdAt: string;
  updatedAt: string;
  sourceRef: Record<string, unknown>;
  eligibility: {
    sourceKind: InboxSourceKind;
    priority: number;
    previewable: boolean;
    published: boolean;
    highRelevance: boolean;
    pushCandidate: boolean;
    reason: string;
  };
  source?: InboxSource;
  target?: InboxTarget;
};

export type InboxSource = {
  status: string;
  reason: string;
  topic?: {
    id: number;
    kind: string;
    category: string;
    title: string;
    detail: string;
    severity: string;
    evidence: Record<string, unknown>[];
    openedAt: string;
    updatedAt: string;
  };
  delivery?: {
    id: number;
    kind: string;
    taskId: number;
    planVersion: number;
    turnId: number | null;
    text: string;
    windowStart?: string;
    windowEnd?: string;
    createdAt: string;
  };
};

export type InboxTarget = {
  kind: 'care_topic' | 'task_reply' | 'unknown';
  route: string;
  params: Record<string, string>;
  available: boolean;
  reason: string;
  /** 可定位锚点：交付回合 id（客户端据此定位到那条来源消息，而不是停在占位路由） */
  anchor: number | null;
};

export type InboxCapability = {
  capability: string;
  version: number;
  serverInbox: boolean;
  managedRules: string[];
  sourceKinds: InboxSourceKind[];
};

export type InboxListing = {
  items: InboxItem[];
  unreadCount: number;
  preview: InboxItem | null;
  nextCursor: number | null;
  capability: InboxCapability;
};

/** 收件箱列表：稳定游标分页（before = 上一页最后一条的 id） */
export function fetchInbox(options: { pet?: number; before?: number; limit?: number } = {}) {
  const params = new URLSearchParams();
  if (options.pet != null) params.set('pet', String(options.pet));
  if (options.before != null) params.set('before', String(options.before));
  if (options.limit != null) params.set('limit', String(options.limit));
  const query = params.toString();
  return request<InboxListing>(`/agent/inbox${query ? `?${query}` : ''}`);
}

/** 条目详情：来源正文 + 稳定目标（深链解析） */
export function fetchInboxItem(id: number) {
  return request<{ item: InboxItem; unreadCount: number }>(`/agent/inbox/${id}`);
}

/** 深链解析：只回稳定路由与目标标识 */
export function resolveInboxItem(id: number) {
  return request<{ item: InboxItem; target: InboxTarget }>(`/agent/inbox/resolve/${id}`);
}

/** 按锚点取回**那一条**来源消息（不依赖会话分页/话题过滤）；跨账号一律 404 */
export function fetchInboxTurn(turnId: number) {
  return request<{ turn: AgentTurn }>(`/agent/inbox/turn/${turnId}`);
}

/** 单条已读（幂等；已读不解除告警） */
export function markInboxRead(id: number) {
  return request<{ item: InboxItem; unreadCount: number }>(`/agent/inbox/${id}/read`, {
    method: 'POST',
  });
}

/** 全部已读：只处理请求生效时可见水位内的条目 */
export function markAllInboxRead(throughId?: number) {
  return request<{ updated: number; watermark: number; unreadCount: number }>(
    '/agent/inbox/read-all',
    { method: 'POST', body: throughId == null ? {} : { throughId } },
  );
}

/** 来源类型的中文标签（列表与预览共用） */
export const INBOX_SOURCE_LABEL: Record<InboxSourceKind, string> = {
  device_fact: '设备事实',
  care: '照料助手',
  reply: '到期回信',
  learning: '学习完成',
};
