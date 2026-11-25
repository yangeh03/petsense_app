/**
 * 伙伴会话 store（W01，agent-w01-spec.md §4.2；W16 改为多会话，docs/agent-w16-spec.md §6.3）：
 * zustand 模块级单例，跨页面共享会话列表，以及每个会话各自的回合、草稿、指向、引用、附件和发送状态。
 * 当前会话的真值是 activeId：URL 参数出现或变化时由页面打开它，抽屉里切换后页面把参数写回。
 * 本地轻量持久化（草稿/滚动/最近回合）由 use-agent-chat 负责，历史本体靠服务端按会话分页。
 */

import { create } from 'zustand';
import type { AgentConversation, AgentReplyMessage, AgentTurn } from '@/services/agent';
import {
  applyDeltaToTurns,
  applyMessageDeltaToTurns,
  applyMessageStatusToTurns,
  applyMessageToTurns,
  dedupeTurns,
  hasConversation,
  isLocalTurn,
  mergeConversations,
  mergeServerTurns,
  noteTurn,
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
  /** 发到哪个会话：在别的会话里重试同一条话时仍发回它 */
  conversation: number;
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

/** 一个会话在界面上的状态。回合只在 loadedFor 等于当前账号 id 时才算已加载。 */
export type AgentConversationChat = {
  turns: AgentTurn[];
  loadedFor: number | null;
  /** 服务端还有更早的一页 */
  hasMore: boolean;
  draft: string;
  target: AgentTarget;
  replyTo: number | undefined;
  /** 消息级引用的预览与发送上下文（未引用时为 undefined） */
  replyToMessage: string | undefined;
  attachments: AgentAttachment[];
  busy: boolean;
  error: string;
  pending: PendingRequest | null;
  scrollY: number;
  /** 恢复这个会话时跟到最新一条，而不是回到 scrollY */
  followLatest: boolean;
};

export type AgentChatState = {
  ownerId: number | null;
  petId: number | null;
  petName: string | null;
  /** 会话摘要，按最近活动倒序；listFor 是拉到这份列表的账号 id */
  list: AgentConversation[];
  listFor: number | null;
  /** 收到过列表里没有的会话的帧：下次用到列表前重新拉一次 */
  listStale: boolean;
  /** 当前会话；没有可打开的会话时为空 */
  activeId: number | null;
  chats: Record<number, AgentConversationChat>;
  /** 最近打开、回合还在内存里的会话，越靠前越新 */
  loaded: number[];
  connection: AgentConnection;
  /** 不属于某个会话的错误（取回宠物资料失败、新建会话失败）：页面顶部的可重试提示 */
  pageError: string;
  /** 服务端已确认当前账号进入双 Agent（列表或回执声明了群聊协议）：输入区才提供「两位都回」 */
  dualAgent: boolean;
  setPet: (pet: { id: number; name: string } | null) => void;
  /** 整表刷新（账号标签随之更新，过期标记清掉） */
  setList: (list: AgentConversation[], forOwner: number | null) => void;
  /** 增量合并会话摘要：接口回执与 WS 的 conversation 帧；带 deletedAt 的从列表移除 */
  upsertConversations: (incoming: AgentConversation | AgentConversation[]) => void;
  /** 本机删除成功：从列表撤掉；删的是当前会话时由页面重新落地 */
  dropConversation: (id: number) => void;
  setActive: (id: number | null) => void;
  /** 准备打开一个会话：记为最近打开，回合缓存按此淘汰 */
  openChat: (id: number) => void;
  /** 从快照恢复：回合先显示出来并算已加载，随后由服务端拉齐 */
  restoreChats: (
    ownerId: number,
    activeId: number | null,
    chats: Record<number, Partial<AgentConversationChat>>,
  ) => void;
  setTurns: (id: number, turns: AgentTurn[], forOwner: number | null) => void;
  syncTurns: (id: number, turns: AgentTurn[]) => void;
  prependOlder: (id: number, turns: AgentTurn[]) => void;
  /** 服务端回合快照（POST 回执或 WS turn 帧）。conversation 是随行的会话摘要，没有时本地补列表项 */
  applyTurn: (turn: AgentTurn, conversation?: AgentConversation | null) => void;
  /** 乐观 UI：本地立即落一条 pending 回合（主人刚发出的那条） */
  applyLocalTurn: (local: LocalAgentTurn, conversationId: number) => void;
  /** 撤下本地乐观回合（发送失败且服务端未确认） */
  removeLocalTurn: (requestId: string) => void;
  /** 没有会话 id 的帧丢弃：帧必须说明自己属于哪个会话 */
  applyDelta: (
    conversationId: number | null,
    turnId: number,
    text: string,
    speaker: AgentTurn['speaker'],
  ) => void;
  /** 双 Agent 群聊：按消息 ID 推送的三种帧 */
  applyMessage: (conversationId: number | null, message: AgentReplyMessage) => void;
  applyMessageDelta: (
    conversationId: number | null,
    turnId: number,
    messageId: string,
    role: AgentReplyMessage['role'],
    text: string,
    version: number,
  ) => void;
  applyMessageStatus: (
    conversationId: number | null,
    turnId: number,
    messageId: string,
    activity: NonNullable<AgentReplyMessage['activity']>,
  ) => void;
  /** 标已读的回执：这些回合带上 readAt，会话未读数以服务端摘要为准 */
  applyRead: (conversation: AgentConversation, turns: AgentTurn[]) => void;
  setDraft: (id: number, draft: string) => void;
  setTarget: (id: number, target: AgentTarget) => void;
  setReplyTo: (id: number, replyTo: number | undefined) => void;
  setReplyToMessage: (id: number, messageId: string | undefined) => void;
  addAttachments: (id: number, items: AgentAttachment[]) => void;
  updateAttachment: (id: number, key: string, patch: Partial<AgentAttachment>) => void;
  removeAttachment: (id: number, key: string) => void;
  clearAttachments: (id: number) => void;
  setBusy: (id: number, busy: boolean) => void;
  setError: (id: number, error: string) => void;
  setScrollY: (id: number, scrollY: number) => void;
  setPending: (id: number, pending: PendingRequest | null) => void;
  setFollowLatest: (id: number, followLatest: boolean) => void;
  setDualAgent: (enabled: boolean) => void;
  setConnection: (connection: AgentConnection) => void;
  setPageError: (error: string) => void;
  resetForUser: (ownerId?: number | null) => void;
};

/** 服务端每页的回合数：拉满一页说明还有更早的 */
const PAGE_SIZE = 40;
/** 内存里保留回合缓存的会话数上限；其余只留草稿等轻状态，再次打开时重新拉 */
const MAX_LOADED_CHATS = 8;

const emptyChat = (): AgentConversationChat => ({
  turns: [],
  loadedFor: null,
  hasMore: false,
  draft: '',
  target: 'auto',
  replyTo: undefined,
  replyToMessage: undefined,
  attachments: [],
  busy: false,
  error: '',
  pending: null,
  scrollY: 0,
  followLatest: true,
});

function chatOf(state: AgentChatState, id: number): AgentConversationChat {
  return state.chats[id] ?? emptyChat();
}

function withChat(
  state: AgentChatState,
  id: number,
  update: (chat: AgentConversationChat) => AgentConversationChat,
): AgentChatState['chats'] {
  return { ...state.chats, [id]: update(chatOf(state, id)) };
}

/** 把 id 记为最近打开；超出上限的会话淘汰回合（在途发送的不淘汰），草稿等轻状态保留。 */
function trackLoaded(
  state: AgentChatState,
  id: number,
): { loaded: number[]; chats: AgentChatState['chats'] } {
  const loaded = [id, ...state.loaded.filter((item) => item !== id)];
  const chats = { ...state.chats };
  for (const evicted of loaded.splice(MAX_LOADED_CHATS)) {
    const chat = chatOf(state, evicted);
    if (!chat.busy && !chat.pending && !chat.turns.some(isLocalTurn)) {
      chats[evicted] = { ...chat, turns: [], loadedFor: null, hasMore: false };
    }
  }
  return { loaded, chats };
}

/** 流式帧只进「已加载、正在看或有在途发送」的会话；其余会话等完整的回合快照。 */
function receivesFrames(state: AgentChatState, conversationId: number): boolean {
  if (conversationId === state.activeId) return true;
  const chat = chatOf(state, conversationId);
  return chat.loadedFor !== null || chat.pending !== null || chat.turns.length > 0;
}

export const useAgentChatStore = create<AgentChatState>((set) => ({
  ownerId: null,
  petId: null,
  petName: null,
  list: [],
  listFor: null,
  listStale: false,
  activeId: null,
  chats: {},
  loaded: [],
  connection: 'closed',
  pageError: '',
  dualAgent: false,
  setPet: (pet) => set({ petId: pet ? pet.id : null, petName: pet ? pet.name : null }),
  setList: (list, forOwner) => set({ list: [...list], listFor: forOwner, listStale: false }),
  upsertConversations: (incoming) =>
    set((state) => {
      const merged = mergeConversations(state.list, incoming);
      return {
        list: merged.list,
        // 当前会话被删掉（比如在另一台设备上）：由页面重新落地
        activeId:
          state.activeId !== null && merged.removed.includes(state.activeId)
            ? null
            : state.activeId,
      };
    }),
  dropConversation: (id) =>
    set((state) => ({
      list: state.list.filter((item) => item.id !== id),
      activeId: state.activeId === id ? null : state.activeId,
    })),
  setActive: (id) => set({ activeId: id }),
  openChat: (id) =>
    set((state) => {
      const tracked = trackLoaded(state, id);
      return {
        loaded: tracked.loaded,
        chats: { ...tracked.chats, [id]: chatOf({ ...state, chats: tracked.chats }, id) },
      };
    }),
  restoreChats: (ownerId, activeId, chats) =>
    set((state) => {
      const next = { ...state.chats };
      for (const [key, saved] of Object.entries(chats)) {
        // 快照恢复按 id 与 requestId 双重去重：同一条主人消息可能既在快照里又刚被服务端确认
        const turns = dedupeTurns(saved.turns?.filter((item) => !isLocalTurn(item)) ?? []);
        next[Number(key)] = { ...emptyChat(), ...saved, turns, loadedFor: ownerId };
      }
      return { ownerId, activeId, chats: next, loaded: Object.keys(chats).map(Number) };
    }),
  setTurns: (id, turns, forOwner) =>
    set((state) => {
      const tracked = trackLoaded(state, id);
      const base = chatOf({ ...state, chats: tracked.chats }, id);
      return {
        loaded: tracked.loaded,
        chats: {
          ...tracked.chats,
          [id]: {
            ...base,
            turns: orderTurns(dedupeTurns(turns)),
            loadedFor: forOwner,
            hasMore: turns.length >= PAGE_SIZE,
          },
        },
      };
    }),
  syncTurns: (id, turns) =>
    set((state) => ({
      chats: withChat(state, id, (chat) => ({
        ...chat,
        turns: mergeServerTurns(chat.turns, turns),
        hasMore: turns.length >= PAGE_SIZE ? chat.hasMore : false,
      })),
    })),
  prependOlder: (id, turns) =>
    set((state) => ({
      chats: withChat(state, id, (chat) =>
        turns.length
          ? {
              ...chat,
              turns: orderTurns(
                dedupeTurns([
                  ...turns,
                  // 本地乐观回合不在服务端分页结果里，保留（它一定比这一页更晚）
                  ...chat.turns.filter((turn) => isLocalTurn(turn) || turn.id < turns[0].id),
                ]),
              ),
              hasMore: turns.length >= PAGE_SIZE,
            }
          : { ...chat, hasMore: false },
      ),
    })),
  applyTurn: (turn, conversation) =>
    set((state) => {
      const id = turn.conversationId ?? state.activeId;
      let { list, listStale, activeId } = state;
      if (conversation) {
        const merged = mergeConversations(list, conversation);
        list = merged.list;
        if (state.activeId !== null && merged.removed.includes(state.activeId)) activeId = null;
      }
      let chats = state.chats;
      if (id !== null) {
        const current = chatOf(state, id);
        const isNew = !current.turns.some(
          (item) => item.id === turn.id || (!!turn.requestId && item.requestId === turn.requestId),
        );
        if (!conversation) {
          if (hasConversation(list, id))
            list = noteTurn(list, id, turn, id === state.activeId, isNew);
          else listStale = true;
        }
        // 没加载过、也不是正在看的会话：不留零散的回合，等打开时整页拉取
        if (
          current.loadedFor !== null ||
          id === state.activeId ||
          current.pending ||
          current.turns.length
        ) {
          chats = withChat(state, id, (chat) => ({
            ...chat,
            turns: reconcileTurn(chat.turns, turn),
          }));
        }
      }
      return { list, listStale, activeId, chats };
    }),
  applyLocalTurn: (local, conversationId) =>
    set((state) => ({
      chats: withChat(state, conversationId, (chat) => ({
        ...chat,
        turns: upsertLocalTurn(chat.turns, local),
      })),
    })),
  removeLocalTurn: (requestId) =>
    set((state) => {
      const ids = Object.keys(state.chats).filter((key) =>
        state.chats[Number(key)].turns.some(
          (turn) => isLocalTurn(turn) && turn.requestId === requestId,
        ),
      );
      if (!ids.length) return state;
      const chats = { ...state.chats };
      for (const key of ids) {
        const id = Number(key);
        chats[id] = { ...chats[id], turns: removeLocalTurn(chats[id].turns, requestId) };
      }
      return { chats };
    }),
  applyDelta: (conversationId, turnId, text, speaker) =>
    set((state) =>
      conversationId !== null && receivesFrames(state, conversationId)
        ? {
            chats: withChat(state, conversationId, (chat) => ({
              ...chat,
              turns: applyDeltaToTurns(chat.turns, turnId, text, speaker, conversationId),
            })),
          }
        : state,
    ),
  applyMessage: (conversationId, message) =>
    set((state) =>
      conversationId !== null && receivesFrames(state, conversationId)
        ? {
            chats: withChat(state, conversationId, (chat) => ({
              ...chat,
              turns: applyMessageToTurns(chat.turns, message, conversationId),
            })),
          }
        : state,
    ),
  applyMessageDelta: (conversationId, turnId, messageId, role, text, version) =>
    set((state) =>
      conversationId !== null && receivesFrames(state, conversationId)
        ? {
            chats: withChat(state, conversationId, (chat) => ({
              ...chat,
              turns: applyMessageDeltaToTurns(
                chat.turns,
                turnId,
                messageId,
                role,
                text,
                version,
                conversationId,
              ),
            })),
          }
        : state,
    ),
  applyMessageStatus: (conversationId, turnId, messageId, activity) =>
    set((state) =>
      conversationId !== null && receivesFrames(state, conversationId)
        ? {
            chats: withChat(state, conversationId, (chat) => ({
              ...chat,
              turns: applyMessageStatusToTurns(chat.turns, turnId, messageId, activity),
            })),
          }
        : state,
    ),
  applyRead: (conversation, turns) =>
    set((state) => ({
      list: mergeConversations(state.list, conversation).list,
      chats: withChat(state, conversation.id, (chat) => ({
        ...chat,
        turns: turns.reduce(reconcileTurn, chat.turns),
      })),
    })),
  setDraft: (id, draft) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, draft })) })),
  setTarget: (id, target) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, target })) })),
  setReplyTo: (id, replyTo) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, replyTo })) })),
  setReplyToMessage: (id, messageId) =>
    set((state) => ({
      chats: withChat(state, id, (chat) => ({ ...chat, replyToMessage: messageId })),
    })),
  addAttachments: (id, items) =>
    set((state) => ({
      chats: withChat(state, id, (chat) => ({
        ...chat,
        attachments: [...chat.attachments, ...items].slice(0, 3),
      })),
    })),
  updateAttachment: (id, key, patch) =>
    set((state) => ({
      chats: withChat(state, id, (chat) => ({
        ...chat,
        attachments: chat.attachments.map((item) =>
          item.key === key ? { ...item, ...patch } : item,
        ),
      })),
    })),
  removeAttachment: (id, key) =>
    set((state) => ({
      chats: withChat(state, id, (chat) => ({
        ...chat,
        attachments: chat.attachments.filter((item) => item.key !== key),
      })),
    })),
  clearAttachments: (id) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, attachments: [] })) })),
  setBusy: (id, busy) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, busy })) })),
  setError: (id, error) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, error })) })),
  setScrollY: (id, scrollY) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, scrollY })) })),
  setPending: (id, pending) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, pending })) })),
  setFollowLatest: (id, followLatest) =>
    set((state) => ({ chats: withChat(state, id, (chat) => ({ ...chat, followLatest })) })),
  setDualAgent: (enabled) => set({ dualAgent: enabled }),
  setConnection: (connection) => set({ connection }),
  setPageError: (error) => set({ pageError: error }),
  resetForUser: (ownerId = null) =>
    set({
      ownerId,
      petId: null,
      petName: null,
      list: [],
      listFor: null,
      listStale: false,
      activeId: null,
      chats: {},
      loaded: [],
      connection: 'closed',
      pageError: '',
      dualAgent: false,
    }),
}));
