/**
 * 伙伴会话 store（W01，agent-w01-spec.md §4.2）：
 * zustand 模块级单例，跨页面共享消息流、主题/指向、草稿与连接状态；
 * 本地轻量持久化（草稿/主题/滚动/最近回合）由 use-agent-chat 负责，历史本体靠服务端分页。
 */

import { create } from 'zustand';
import type { AgentTurn } from '@/services/agent';

export type AgentTarget = AgentTurn['target'];
export type AgentConnection = 'connecting' | 'open' | 'closed' | 'unauthorized';

/** 乐观发送上下文：同键同内容重试复用，与服务的幂等键对齐 */
export type PendingRequest = {
  text: string;
  target: AgentTarget;
  pet: number;
  key: string;
  topic?: number;
  replyTo?: number;
};

export type AgentChatState = {
  petId: number | null;
  petName: string | null;
  loadedFor: number | null;
  turns: AgentTurn[];
  topic: number | undefined;
  replyTo: number | undefined;
  draft: string;
  target: AgentTarget;
  busy: boolean;
  error: string;
  connection: AgentConnection;
  scrollY: number;
  pending: PendingRequest | null;
  setPet: (pet: { id: number; name: string } | null) => void;
  setLoadedFor: (userId: number | null) => void;
  setTurns: (turns: AgentTurn[]) => void;
  prependOlder: (turns: AgentTurn[]) => void;
  applyTurn: (turn: AgentTurn) => void;
  applyDelta: (petId: number, turnId: number, text: string, speaker: AgentTurn['speaker']) => void;
  setDraft: (draft: string) => void;
  setTarget: (target: AgentTarget) => void;
  setTopic: (topic: number | undefined) => void;
  setReplyTo: (replyTo: number | undefined) => void;
  setBusy: (busy: boolean) => void;
  setError: (error: string) => void;
  setConnection: (connection: AgentConnection) => void;
  setScrollY: (scrollY: number) => void;
  setPending: (pending: PendingRequest | null) => void;
  resetForUser: () => void;
};

function upsert(turns: AgentTurn[], turn: AgentTurn) {
  const index = turns.findIndex((item) => item.id === turn.id);
  if (index === -1) {
    return [...turns, turn].sort((a, b) => a.id - b.id);
  }
  const next = turns.slice();
  next[index] = turn;
  return next;
}

export const useAgentChatStore = create<AgentChatState>((set) => ({
  petId: null,
  petName: null,
  loadedFor: null,
  turns: [],
  topic: undefined,
  replyTo: undefined,
  draft: '',
  target: 'auto',
  busy: false,
  error: '',
  connection: 'closed',
  scrollY: 0,
  pending: null,
  setPet: (pet) => set({ petId: pet ? pet.id : null, petName: pet ? pet.name : null }),
  setLoadedFor: (userId) => set({ loadedFor: userId }),
  setTurns: (turns) => set({ turns }),
  prependOlder: (older) =>
    set((state) => ({
      turns: older.length
        ? [...older, ...state.turns.filter((turn) => turn.id < older[0].id)]
        : state.turns,
    })),
  applyTurn: (turn) => set((state) => ({ turns: upsert(state.turns, turn) })),
  applyDelta: (petId, turnId, text, speaker) =>
    set((state) => {
      if (state.petId !== petId) return state;
      const index = state.turns.findIndex((item) => item.id === turnId);
      if (index === -1) {
        // 尚未本地建回合（发送请求还在路上）：占位，终态 turn 帧或 POST 返回会覆盖
        const placeholder: AgentTurn = {
          id: turnId,
          requestId: '',
          text: '',
          speaker,
          readAt: null,
          target: 'auto',
          reply: text,
          status: 'pending',
          createdAt: new Date().toISOString(),
          topicId: state.topic ?? null,
          replyTo: null,
          sources: [],
          blocks: null,
        };
        return { turns: [...state.turns, placeholder] };
      }
      const turns = state.turns.slice();
      const current = turns[index];
      if (current.status !== 'pending') return state; // 终态以服务端已保存结果为准
      turns[index] = { ...current, reply: text, speaker };
      return { turns };
    }),
  setDraft: (draft) => set({ draft }),
  setTarget: (target) => set({ target }),
  setTopic: (topic) => set({ topic, replyTo: undefined }),
  setReplyTo: (replyTo) => set({ replyTo }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  setConnection: (connection) => set({ connection }),
  setScrollY: (scrollY) => set({ scrollY }),
  setPending: (pending) => set({ pending }),
  resetForUser: () =>
    set({
      petId: null,
      petName: null,
      loadedFor: null,
      turns: [],
      topic: undefined,
      replyTo: undefined,
      draft: '',
      target: 'auto',
      busy: false,
      error: '',
      scrollY: 0,
      pending: null,
    }),
}));
