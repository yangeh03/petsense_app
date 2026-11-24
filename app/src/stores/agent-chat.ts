/**
 * 伙伴会话 store（W01，agent-w01-spec.md §4.2）：
 * zustand 模块级单例，跨页面共享消息流、主题/指向、草稿与连接状态；
 * 本地轻量持久化（草稿/主题/滚动/最近回合）由 use-agent-chat 负责，历史本体靠服务端分页。
 */

import { create } from 'zustand';
import type { AgentReplyMessage, AgentTurn } from '@/services/agent';
import {
  applyDeltaToTurns,
  applyMessageDeltaToTurns,
  applyMessageStatusToTurns,
  applyMessageToTurns,
  dedupeTurns,
  isLocalTurn,
  mergeServerTurns,
  orderTurns,
  reconcileTurn,
  removeLocalTurn,
  upsertLocalTurn,
  type LocalAgentTurn,
} from '@/services/agent-chat-turns';

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
  /** 消息级引用（群聊协议）：精确到被引用回合里的某一条回复 */
  replyToMessage?: string;
  images?: string[];
};

/** W07 输入区图片附件：本地预览 + 逐张上传状态；done 后持服务端 imageId 供发送引用 */
export type AgentAttachment = {
  key: string;
  localUri: string | null;
  width: number;
  height: number;
  imageId: string | null;
  status: 'uploading' | 'done' | 'failed';
  /** 上传失败原因（服务端结构化消息，如超配额提示清理）；仅 failed 态有意义 */
  failReason?: string;
};

export type AgentChatState = {
  ownerId: number | null;
  petId: number | null;
  petName: string | null;
  loadedFor: number | null;
  turns: AgentTurn[];
  topic: number | undefined;
  replyTo: number | undefined;
  /** 消息级引用的预览与发送上下文（未引用时为 undefined） */
  replyToMessage: string | undefined;
  draft: string;
  target: AgentTarget;
  attachments: AgentAttachment[];
  busy: boolean;
  error: string;
  connection: AgentConnection;
  scrollY: number;
  pending: PendingRequest | null;
  /** 服务端已确认当前账号进入双 Agent（列表或回执声明了群聊协议）：输入区才提供「两位都回」 */
  dualAgent: boolean;
  setPet: (pet: { id: number; name: string } | null) => void;
  setLoadedFor: (userId: number | null) => void;
  setTurns: (turns: AgentTurn[]) => void;
  /** 拉齐服务端列表：已加载的回合按消息逐条合并，迟到的旧快照不盖掉 WS 已推来的新状态 */
  syncTurns: (turns: AgentTurn[]) => void;
  prependOlder: (turns: AgentTurn[]) => void;
  applyTurn: (turn: AgentTurn) => void;
  /** 乐观 UI：本地立即落一条 pending 回合（主人刚发出的那条） */
  applyLocalTurn: (turn: LocalAgentTurn) => void;
  /** 撤下本地乐观回合（发送失败且服务端未确认） */
  removeLocalTurn: (requestId: string) => void;
  applyDelta: (petId: number, turnId: number, text: string, speaker: AgentTurn['speaker']) => void;
  /** 双 Agent 群聊：按消息 ID 推送的三种帧 */
  applyMessage: (petId: number, message: AgentReplyMessage) => void;
  applyMessageDelta: (
    petId: number,
    turnId: number,
    messageId: string,
    role: AgentReplyMessage['role'],
    text: string,
    version: number,
  ) => void;
  applyMessageStatus: (
    petId: number,
    turnId: number,
    messageId: string,
    activity: NonNullable<AgentReplyMessage['activity']>,
  ) => void;
  setDraft: (draft: string) => void;
  setTarget: (target: AgentTarget) => void;
  setTopic: (topic: number | undefined) => void;
  setReplyTo: (replyTo: number | undefined) => void;
  setReplyToMessage: (messageId: string | undefined) => void;
  setDualAgent: (enabled: boolean) => void;
  addAttachments: (items: AgentAttachment[]) => void;
  updateAttachment: (key: string, patch: Partial<AgentAttachment>) => void;
  removeAttachment: (key: string) => void;
  clearAttachments: () => void;
  setBusy: (busy: boolean) => void;
  setError: (error: string) => void;
  setConnection: (connection: AgentConnection) => void;
  setScrollY: (scrollY: number) => void;
  setPending: (pending: PendingRequest | null) => void;
  resetForUser: (ownerId?: number | null) => void;
};

export const useAgentChatStore = create<AgentChatState>((set) => ({
  ownerId: null,
  petId: null,
  petName: null,
  loadedFor: null,
  turns: [],
  topic: undefined,
  replyTo: undefined,
  replyToMessage: undefined,
  draft: '',
  target: 'auto',
  attachments: [],
  busy: false,
  error: '',
  connection: 'closed',
  scrollY: 0,
  pending: null,
  dualAgent: false,
  setPet: (pet) => set({ petId: pet ? pet.id : null, petName: pet ? pet.name : null }),
  setLoadedFor: (userId) => set({ loadedFor: userId }),
  setTurns: (turns) => set({ turns: orderTurns(dedupeTurns(turns)) }),
  syncTurns: (turns) => set((state) => ({ turns: mergeServerTurns(state.turns, turns) })),
  prependOlder: (older) =>
    set((state) => ({
      turns: orderTurns(
        dedupeTurns(
          older.length
            ? [
                ...older,
                // 本地乐观回合不在服务端分页结果里，保留（它一定比这一页更晚）
                ...state.turns.filter((turn) => isLocalTurn(turn) || turn.id < older[0].id),
              ]
            : state.turns,
        ),
      ),
    })),
  applyTurn: (turn) => set((state) => ({ turns: reconcileTurn(state.turns, turn) })),
  applyLocalTurn: (local) => set((state) => ({ turns: upsertLocalTurn(state.turns, local) })),
  removeLocalTurn: (requestId) =>
    set((state) => ({ turns: removeLocalTurn(state.turns, requestId) })),
  applyDelta: (petId, turnId, text, speaker) =>
    set((state) => {
      if (state.petId !== petId) return state;
      return { turns: applyDeltaToTurns(state.turns, turnId, text, speaker, state.topic ?? null) };
    }),
  applyMessage: (petId, message) =>
    set((state) =>
      state.petId === petId ? { turns: applyMessageToTurns(state.turns, message) } : state,
    ),
  applyMessageDelta: (petId, turnId, messageId, role, text, version) =>
    set((state) =>
      state.petId === petId
        ? { turns: applyMessageDeltaToTurns(state.turns, turnId, messageId, role, text, version) }
        : state,
    ),
  applyMessageStatus: (petId, turnId, messageId, activity) =>
    set((state) =>
      state.petId === petId
        ? { turns: applyMessageStatusToTurns(state.turns, turnId, messageId, activity) }
        : state,
    ),
  setDraft: (draft) => set({ draft }),
  setTarget: (target) => set({ target }),
  setTopic: (topic) => set({ topic, replyTo: undefined, replyToMessage: undefined }),
  setReplyTo: (replyTo) => set({ replyTo }),
  setReplyToMessage: (messageId) => set({ replyToMessage: messageId }),
  setDualAgent: (enabled) => set({ dualAgent: enabled }),
  addAttachments: (items) =>
    set((state) => ({ attachments: [...state.attachments, ...items].slice(0, 3) })),
  updateAttachment: (key, patch) =>
    set((state) => ({
      attachments: state.attachments.map((item) =>
        item.key === key ? { ...item, ...patch } : item,
      ),
    })),
  removeAttachment: (key) =>
    set((state) => ({ attachments: state.attachments.filter((item) => item.key !== key) })),
  clearAttachments: () => set({ attachments: [] }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  setConnection: (connection) => set({ connection }),
  setScrollY: (scrollY) => set({ scrollY }),
  setPending: (pending) => set({ pending }),
  resetForUser: (ownerId = null) =>
    set({
      ownerId,
      petId: null,
      petName: null,
      loadedFor: null,
      turns: [],
      topic: undefined,
      replyTo: undefined,
      replyToMessage: undefined,
      draft: '',
      target: 'auto',
      attachments: [],
      busy: false,
      error: '',
      scrollY: 0,
      pending: null,
      dualAgent: false,
    }),
}));
