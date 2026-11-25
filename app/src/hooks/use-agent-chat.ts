/**
 * 伙伴页当前会话的生命周期（W01，agent-w01-spec.md §4.2/§4.4；W16 起按会话隔离，docs/agent-w16-spec.md §6）：
 * - 当前会话的真值在 store 的 activeId。URL 带 conversationId 就打开它；不带时按落地规则选一个
 *   （本机记住的上次会话，其次最近活动的第一个，列表为空就新建一个），并把 id 写回 URL；
 * - 回合按会话加载与缓存；WS 帧按 conversationId 进对应的会话，断线降级轮询；
 * - 发送乐观 UI + 同键重试去重；失败保留原话与图片；W07 图片先传后发；
 * - AsyncStorage 按用户保存每个会话的草稿、指向、引用、滚动位置和最近回合。
 * - 双 Agent 群聊（docs/agent-dual-agent-chat-design.md §4）：是否进入双 Agent 以服务端声明为准；
 *   逐条回复按消息 ID/版本合并；失败的发言可只重试那一条。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useIsFocused, useRouter } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import { agentChannel } from '@/services/agent-channel';
import {
  AGENT_GROUP_PROTOCOL,
  createAgentConversation,
  fetchAgentObservation,
  fetchAgentTurns,
  markConversationRead,
  retryAgentTurn,
  sendAgentTurn,
  uploadAgentImage,
  type AgentTurn,
} from '@/services/agent';
import { ApiError, fetchDashboard, getToken } from '@/services/api';
import { scrollRestorePlan } from '@/services/chat-restore';
import {
  isLocalTurn,
  landingConversation,
  nextLocalTurnId,
  sendableTarget,
  type LocalAgentTurn,
} from '@/services/agent-chat-turns';
import {
  AGENT_JPEG_QUALITY,
  AGENT_UPLOAD_MAX_EDGE,
  normalizeForUpload,
} from '@/services/normalize-image';
import { refreshCareRules } from '@/services/care-rules';
import { fetchConversationList } from '@/hooks/use-agent-conversations';
import {
  useAgentChatStore,
  type AgentAttachment,
  type AgentChatState,
  type AgentConversationChat,
  type AgentTarget,
  type PendingRequest,
} from '@/stores/agent-chat';
import { useCompanionSettingsStore } from '@/stores/companion-settings';

const STORAGE_PREFIX = 'petsense.agent.chat.';
const POLL_INTERVAL_MS = 800;
const SNAPSHOT_DELAY_MS = 400;
const KEEP_TURNS = 20;
const KEEP_CHATS = 5;
const MAX_ATTACHMENTS = 3;

type SavedChat = Pick<
  AgentConversationChat,
  'draft' | 'target' | 'replyTo' | 'replyToMessage' | 'scrollY' | 'followLatest' | 'hasMore'
> & { turns: AgentTurn[] };
type Snapshot = { activeId: number | null; chats: Record<number, SavedChat> };

const EMPTY_CHAT: AgentConversationChat = {
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
};

/** 本地快照只留最近几个会话的轻状态与最近回合（本地乐观回合不进快照） */
function snapshotOf(state: AgentChatState): Snapshot {
  const ids = [
    ...new Set([...(state.activeId !== null ? [state.activeId] : []), ...state.loaded]),
  ].slice(0, KEEP_CHATS);
  const chats: Record<number, SavedChat> = {};
  for (const id of ids) {
    const chat = state.chats[id];
    if (!chat || (!chat.draft && chat.loadedFor === null)) continue;
    chats[id] = {
      draft: chat.draft,
      target: chat.target,
      replyTo: chat.replyTo,
      replyToMessage: chat.replyToMessage,
      scrollY: chat.scrollY,
      followLatest: chat.followLatest,
      hasMore: chat.hasMore,
      turns: chat.turns.filter((turn) => !isLocalTurn(turn)).slice(-KEEP_TURNS),
    };
  }
  return { activeId: state.activeId, chats };
}

async function loadSnapshot(userId: number): Promise<Snapshot | null> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_PREFIX + userId);
    return raw ? (JSON.parse(raw) as Snapshot) : null;
  } catch {
    return null;
  }
}

/** 「两位都回」发给了未开放双 Agent 的账号：服务端在落库前拒绝，可用同一请求键改按未指定重发 */
function isDualAgentUnavailable(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 400 &&
    (error.payload as { code?: string } | undefined)?.code === 'dual_agent_unavailable'
  );
}

/** 服务端在当前账号上声明了是否进入双 Agent：以它为准，不从本地推断 */
function learnDualAgent(enabled: boolean) {
  const state = useAgentChatStore.getState();
  if (state.dualAgent === enabled) return;
  state.setDualAgent(enabled);
  if (!enabled) {
    for (const [id, chat] of Object.entries(state.chats)) {
      if (chat.target === 'both') state.setTarget(Number(id), 'auto');
    }
  }
}

/** 这条回合在哪个会话的缓存里（重试失败时把提示落回它所在的会话） */
function conversationOfTurn(state: AgentChatState, turnId: number): number | null {
  const found = Object.entries(state.chats).find(([, chat]) =>
    chat.turns.some((turn) => turn.id === turnId),
  );
  return found ? Number(found[0]) : null;
}

/** 当前会话的未读数（列表摘要 + 已加载回合里的未读），用来决定是否需要标已读 */
function unreadOf(state: AgentChatState): number {
  if (state.activeId === null) return 0;
  const loaded = state.chats[state.activeId]?.turns ?? [];
  return (
    (state.list.find((item) => item.id === state.activeId)?.unread ?? 0) +
    loaded.filter((turn) => turn.status !== 'pending' && !turn.readAt).length
  );
}

export function useAgentChat(
  user: { id: number } | null,
  options: { requestedId: number | null; paused: boolean },
) {
  const { requestedId, paused } = options;
  const userId = user?.id ?? null;
  const router = useRouter();
  const focused = useIsFocused();
  const [learningDays, setLearningDays] = useState<number | null>(null);
  const [soundDays, setSoundDays] = useState(0);
  const [learningState, setLearningState] = useState('learning');
  const [soundState, setSoundState] = useState('learning');
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const restoredFor = useRef<number | null>(null);
  const wasOpen = useRef(false);
  const remembered = useRef<number | null>(null);
  const landing = useRef(false);
  const reading = useRef(false);
  const loading = useRef(new Set<number>());

  const ownerId = useAgentChatStore((state) => state.ownerId);
  const activeId = useAgentChatStore((state) => state.activeId);
  const connection = useAgentChatStore((state) => state.connection);
  const list = useAgentChatStore((state) => state.list);
  const listFor = useAgentChatStore((state) => state.listFor);
  const chats = useAgentChatStore((state) => state.chats);
  const storedChat = useAgentChatStore((state) =>
    state.activeId !== null ? state.chats[state.activeId] : undefined,
  );
  // 账号标签在渲染时推导：换了账号而 store 还没重置的这一帧，不展示上一个账号的内容
  const own = ownerId === userId && userId !== null;
  const chat = own && storedChat ? storedChat : EMPTY_CHAT;
  const conversation =
    own && activeId !== null ? (list.find((item) => item.id === activeId) ?? null) : null;
  const petId = useAgentChatStore((state) => state.petId);

  /** 打开一个会话：记为最近打开并设为当前会话，回合按需加载（深链、落地规则与抽屉切换共用） */
  const openConversation = useCallback((id: number) => {
    const state = useAgentChatStore.getState();
    state.openChat(id);
    state.setActive(id);
  }, []);

  /** 抽屉里切换会话：把 id 写回 URL，并清掉上一次深链留下的定位参数 */
  const switchConversation = useCallback(
    (id: number) => {
      if (id !== useAgentChatStore.getState().activeId) openConversation(id);
      router.setParams({
        conversationId: String(id),
        turnId: undefined,
        deliveryId: undefined,
        taskId: undefined,
        topicId: undefined,
        inboxId: undefined,
      });
    },
    [openConversation, router],
  );

  const loadConversation = useCallback(
    async (id: number, force = false) => {
      const state = useAgentChatStore.getState();
      if (state.petId === null || userId === null) return;
      if (!force && state.chats[id]?.loadedFor === userId) return;
      if (loading.current.has(id)) return;
      const session = getToken();
      const currentSession = () => !!session && getToken() === session;
      loading.current.add(id);
      if (state.activeId === id) setLoadingId(id);
      try {
        const data = await fetchAgentTurns(state.petId, id);
        if (!currentSession()) return;
        const current = useAgentChatStore.getState();
        learnDualAgent(data.dualAgent === true);
        current.setTurns(id, data.turns, userId);
        if (data.conversation) current.upsertConversations(data.conversation);
        current.setError(id, '');
      } catch (error) {
        if (!currentSession()) return;
        const gone = error instanceof ApiError && error.status === 404;
        useAgentChatStore
          .getState()
          .setError(id, gone ? '这个会话已经不存在了' : '暂时没能取回这个会话的聊天，点这里重试');
      } finally {
        loading.current.delete(id);
        if (currentSession()) {
          // 切换会话时前后两个加载可能重叠：当前会话还在取就继续显示加载态
          const active = useAgentChatStore.getState().activeId;
          setLoadingId(active !== null && loading.current.has(active) ? active : null);
        }
      }
    },
    [userId],
  );

  /** 落地规则（spec §6.1）：没有指定会话也没有当前会话时，选一个或新建一个，并把 id 写回 URL */
  const land = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (requestedId !== null || state.activeId !== null) return;
    if (state.petId === null || userId === null || state.listFor !== userId) return;
    if (landing.current) return;
    landing.current = true;
    const session = getToken();
    try {
      let id = landingConversation(state.list, remembered.current)?.id ?? null;
      if (id === null) {
        const data = await createAgentConversation(state.petId);
        if (!session || getToken() !== session) return;
        useAgentChatStore.getState().upsertConversations(data.conversation);
        id = data.conversation.id;
      }
      const current = useAgentChatStore.getState();
      if (current.activeId !== null) return; // 等待期间已经打开了别的会话
      openConversation(id);
      router.setParams({ conversationId: String(id) });
    } catch {
      if (!!session && getToken() === session) {
        useAgentChatStore.getState().setPageError('没能打开会话，点这里重试');
      }
    } finally {
      landing.current = false;
    }
  }, [openConversation, requestedId, router, userId]);

  const refreshList = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (state.petId === null || state.ownerId === null) return;
    try {
      await fetchConversationList(state.petId, state.ownerId);
    } catch {
      // 下次聚焦再拉齐
    }
  }, []);

  const reload = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (!userId) {
      state.resetForUser();
      setLearningDays(null);
      return;
    }
    const session = getToken();
    const currentSession = () => !!session && getToken() === session;
    try {
      const data = await fetchDashboard();
      if (!currentSession()) return;
      const current = useAgentChatStore.getState();
      current.setPet(data.pet);
      // W09：按当前宠物探测后端受管理告警规则（切宠/退出时内部恢复本地逻辑）
      void refreshCareRules(data.pet?.id ?? null);
      if (data.pet) {
        await fetchConversationList(data.pet.id, userId);
        if (!currentSession()) return;
        const end = new Date();
        const start = new Date(end.getTime() - 89 * 86400000);
        const facts = await fetchAgentObservation(
          data.pet.id,
          start.toISOString(),
          end.toISOString(),
        );
        if (!currentSession()) return;
        setLearningDays(facts.learning.validDays);
        setLearningState(facts.learning.state);
        setSoundDays(
          facts.learningDimensions.find((value) => value.dimension === 'sounds')?.validDays ?? 0,
        );
        setSoundState(
          facts.learningDimensions.find((value) => value.dimension === 'sounds')?.state ??
            'learning',
        );
        // 当前会话重新拉齐（加载失败后的重试、冷启动恢复后都以服务端为准）
        const latest = useAgentChatStore.getState();
        if (latest.activeId !== null) void loadConversation(latest.activeId, true);
      }
      useAgentChatStore.getState().setPageError('');
    } catch {
      if (!currentSession()) return;
      useAgentChatStore.getState().setPageError('暂时没能取回我们的聊天，点这里重试');
    }
  }, [userId, loadConversation]);

  const reloadRef = useRef(reload);
  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);

  /** 用户切换：先恢复本地快照（首屏不空白），再拉服务端 */
  useEffect(() => {
    if (restoredFor.current === userId) return;
    restoredFor.current = userId;
    wasOpen.current = false;
    useAgentChatStore.getState().resetForUser(userId);
    if (!userId) return;
    let active = true;
    void loadSnapshot(userId).then((snapshot) => {
      if (!active) return;
      remembered.current = snapshot?.activeId ?? null;
      // 没有可恢复的滚动位置时跟到最新一条（W14 F1）：由列表在内容布局后滚到底
      useAgentChatStore
        .getState()
        .restoreChats(
          userId,
          snapshot?.activeId ?? null,
          Object.fromEntries(
            Object.entries(snapshot?.chats ?? {}).map(([id, saved]) => [
              id,
              { ...saved, followLatest: scrollRestorePlan(saved).followLatest },
            ]),
          ),
        );
      void reloadRef.current();
    });
    return () => {
      active = false;
    };
  }, [userId]);

  // URL 指定了会话就打开它（深链、抽屉切换、委托簿回原会话）
  useEffect(() => {
    if (requestedId !== null && requestedId !== useAgentChatStore.getState().activeId) {
      openConversation(requestedId);
    }
  }, [openConversation, requestedId]);

  // 当前会话的回合还没从服务端拉过：加载
  useEffect(() => {
    if (activeId === null) return;
    if (useAgentChatStore.getState().chats[activeId]?.loadedFor !== userId) {
      void Promise.resolve().then(() => loadConversation(activeId)); // 加载会立刻更新加载态，放到下一拍
    }
  }, [activeId, loadConversation, userId]);

  useEffect(() => {
    void land();
  }, [land, list, listFor, activeId, petId]);

  /** 本地快照持久化（轻量防抖）：按会话保存草稿、指向、引用、滚动位置与最近回合 */
  useEffect(() => {
    if (!userId) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = useAgentChatStore.subscribe((state) => {
      if (state.ownerId !== userId) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        void AsyncStorage.setItem(
          STORAGE_PREFIX + userId,
          JSON.stringify(snapshotOf(useAgentChatStore.getState())),
        ).catch(() => {});
      }, SNAPSHOT_DELAY_MS);
    });
    return () => {
      unsubscribe();
      if (timer) clearTimeout(timer);
    };
  }, [userId]);

  /** 重连后以服务端已保存结果为准：列表过期时重拉，当前会话与有未完成回合的会话逐个拉齐 */
  const resync = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (state.petId === null || state.ownerId === null) return;
    const session = getToken();
    if (state.listStale) {
      try {
        await fetchConversationList(state.petId, state.ownerId);
      } catch {
        // 下次聚焦再拉齐
      }
    }
    const latest = useAgentChatStore.getState();
    if (!session || getToken() !== session) return;
    for (const id of latest.loaded) {
      if (latest.chats[id]?.turns.some((turn) => turn.status === 'pending')) {
        void loadConversation(id, true);
      }
    }
    if (latest.activeId !== null) void loadConversation(latest.activeId, true);
  }, [loadConversation]);

  /** WS：状态 + 帧 + 重连拉齐（以服务端已保存结果为准） */
  useEffect(() => {
    if (!userId) return;
    const session = getToken();
    let active = true;
    const currentSession = () => active && !!session && getToken() === session;
    const unsubscribeStatus = agentChannel.subscribeStatus((status) => {
      if (!currentSession()) return;
      useAgentChatStore.getState().setConnection(status);
      if (status === 'open') {
        if (wasOpen.current || useAgentChatStore.getState().loaded.length) void resync();
        wasOpen.current = true;
      }
    });
    const unsubscribeFrames = agentChannel.subscribe((frame) => {
      if (!currentSession()) return;
      const store = useAgentChatStore.getState();
      // 流式与消息帧必须说明自己属于哪个会话；没有会话 id 的丢弃，等完整的回合快照
      if (frame.type === 'turn') {
        store.applyTurn(frame.turn, frame.conversation);
      } else if (frame.type === 'conversation') {
        store.upsertConversations(frame.conversation);
      } else if (frame.type === 'delta') {
        store.applyDelta(frame.conversationId, frame.turnId, frame.text, frame.speaker);
      } else if (frame.type === 'message_delta') {
        store.applyMessageDelta(
          frame.conversationId,
          frame.turnId,
          frame.messageId,
          frame.role,
          frame.text,
          frame.version,
        );
      } else if (frame.type === 'message') {
        store.applyMessage(frame.conversationId, frame.message);
      } else if (frame.type === 'message_status') {
        store.applyMessageStatus(frame.conversationId, frame.turnId, frame.messageId, frame.status);
      }
    });
    agentChannel.reconnect();
    return () => {
      active = false;
      unsubscribeStatus();
      unsubscribeFrames();
    };
  }, [userId, resync]);

  /** 降级轮询：仅在无 WS 连接且有待完成回合时工作（沿用旧行为），按会话各自拉齐 */
  const pendingKey = useMemo(
    () =>
      Object.entries(chats)
        .filter(
          ([, item]) =>
            item.loadedFor !== null && item.turns.some((turn) => turn.status === 'pending'),
        )
        .map(([id]) => id)
        .join(','),
    [chats],
  );
  useEffect(() => {
    if (!userId || !petId || !pendingKey || connection === 'open') return;
    const session = getToken();
    let active = true;
    const ids = pendingKey.split(',').map(Number);
    const timer = setInterval(() => {
      for (const id of ids) {
        void fetchAgentTurns(petId, id)
          .then((data) => {
            if (!active || !session || getToken() !== session) return;
            const state = useAgentChatStore.getState();
            learnDualAgent(data.dualAgent === true);
            state.syncTurns(id, data.turns);
            if (data.conversation) state.upsertConversations(data.conversation);
          })
          .catch(() => {});
      }
    }, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [userId, petId, pendingKey, connection]);

  /** 把当前会话的未读标为已读（整个会话，不只是已加载的那几条）；没有未读时不发请求 */
  const markRead = useCallback(async () => {
    const state = useAgentChatStore.getState();
    const id = state.activeId;
    if (id === null || state.ownerId === null || reading.current || unreadOf(state) === 0) return;
    const session = getToken();
    reading.current = true;
    try {
      const result = await markConversationRead(id);
      if (!session || getToken() !== session) return;
      const latest = useAgentChatStore.getState();
      if (latest.activeId !== id) return;
      latest.applyRead(result.conversation, result.turns);
      // 请求期间又来了新回合：未读数变化会让页面的 effect 再标一次
    } catch {
      // 下次聚焦重试
    } finally {
      reading.current = false;
    }
  }, []);

  // 伙伴页在前台、抽屉没打开时，当前会话的新回合到达即标已读
  const activeUnread = useAgentChatStore(unreadOf);
  useEffect(() => {
    if (!focused || paused || userId === null || activeUnread === 0) return;
    void markRead();
  }, [focused, paused, userId, activeUnread, markRead]);

  /** W07：单张上传（选中即传，失败标红可重试；服务端内容寻址，重试零成本） */
  const uploadOne = useCallback(async (attachment: AgentAttachment, conversationId: number) => {
    const session = getToken();
    const currentSession = () => !!session && getToken() === session;
    const state = useAgentChatStore.getState();
    if (!state.petId || !attachment.localUri) {
      state.updateAttachment(conversationId, attachment.key, { status: 'failed' });
      return;
    }
    try {
      const meta = await uploadAgentImage(state.petId, {
        uri: attachment.localUri,
        name: 'photo.jpg',
        type: 'image/jpeg',
      });
      if (!currentSession()) return;
      useAgentChatStore.getState().updateAttachment(conversationId, attachment.key, {
        imageId: meta.id,
        width: meta.width ?? attachment.width,
        height: meta.height ?? attachment.height,
        status: 'done',
        failReason: undefined,
      });
    } catch (error) {
      if (!currentSession()) return;
      // 服务端结构化消息如实透出（如超配额「请先清理历史图片」），网络失败用通用文案
      const reason = error instanceof ApiError ? error.message : '图片上传失败，请检查网络后重试';
      useAgentChatStore.getState().updateAttachment(conversationId, attachment.key, {
        status: 'failed',
        failReason: reason,
      });
    }
  }, []);

  /** W07：相册（多选）/拍照（单张）→ 压缩（1280/q0.8，D2）→ 逐张上传，附件属于当前会话 */
  const pickImages = useCallback(
    async (source: 'library' | 'camera') => {
      const session = getToken();
      const currentSession = () => !!session && getToken() === session;
      const state = useAgentChatStore.getState();
      const conversationId = state.activeId;
      if (!state.petId || conversationId === null) return;
      const attachments = state.chats[conversationId]?.attachments ?? [];
      const limit = MAX_ATTACHMENTS - attachments.length;
      if (limit <= 0) return;
      const pickerOptions: ImagePicker.ImagePickerOptions = {
        mediaTypes: ['images'],
        quality: 0.8,
      };
      const result =
        source === 'library'
          ? await ImagePicker.launchImageLibraryAsync({
              ...pickerOptions,
              allowsMultipleSelection: true,
              selectionLimit: limit,
            })
          : await ImagePicker.launchCameraAsync(pickerOptions);
      if (result.canceled || !currentSession()) return;
      for (const asset of result.assets.slice(0, limit)) {
        const key = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        let normalized: Awaited<ReturnType<typeof normalizeForUpload>>;
        try {
          normalized = await normalizeForUpload(asset, {
            maxEdge: AGENT_UPLOAD_MAX_EDGE,
            quality: AGENT_JPEG_QUALITY,
          });
        } catch {
          continue; // 单张压缩失败跳过该张，不阻断其余选择
        }
        if (!currentSession()) return;
        const attachment: AgentAttachment = {
          key,
          localUri: normalized.uri,
          width: asset.width ?? 0,
          height: asset.height ?? 0,
          imageId: null,
          status: 'uploading',
        };
        useAgentChatStore.getState().addAttachments(conversationId, [attachment]);
        void uploadOne(attachment, conversationId);
      }
    },
    [uploadOne],
  );

  const retryAttachment = useCallback(
    (key: string) => {
      const state = useAgentChatStore.getState();
      const conversationId = state.activeId;
      const attachment =
        conversationId === null
          ? undefined
          : state.chats[conversationId]?.attachments.find((item) => item.key === key);
      if (conversationId === null || !attachment || attachment.status === 'uploading') return;
      state.updateAttachment(conversationId, key, { status: 'uploading' });
      void uploadOne({ ...attachment, status: 'uploading' }, conversationId);
    },
    [uploadOne],
  );

  const removeAttachment = useCallback((key: string) => {
    const conversationId = useAgentChatStore.getState().activeId;
    if (conversationId !== null) useAgentChatStore.getState().removeAttachment(conversationId, key);
  }, []);

  const send = useCallback(async () => {
    const session = getToken();
    const currentSession = () => !!session && getToken() === session;
    const state = useAgentChatStore.getState();
    const conversationId = state.activeId;
    const current = conversationId === null ? undefined : state.chats[conversationId];
    if (!state.petId || conversationId === null || !current || current.busy) return;
    // 未全完成不可发（含失败，spec §4.1）：失败图留在预览条等重试或移除，不随消息发出也不被清掉
    if (current.attachments.some((item) => item.status !== 'done')) return;
    const draft = current.draft.trim();
    const images = current.attachments
      .filter((item) => item.status === 'done' && item.imageId)
      .map((item) => item.imageId as string);
    if (!draft && !images.length) return;
    const sameContent =
      !!current.pending &&
      current.pending.text === draft &&
      current.pending.target === current.target &&
      current.pending.pet === state.petId &&
      current.pending.conversation === conversationId &&
      current.pending.replyTo === current.replyTo &&
      current.pending.replyToMessage === current.replyToMessage &&
      (current.pending.images ?? []).join(',') === images.join(',');
    const pending: PendingRequest =
      sameContent && current.pending
        ? current.pending
        : {
            text: draft,
            target: sendableTarget(current.target, state.dualAgent),
            pet: state.petId,
            conversation: conversationId,
            replyTo: current.replyTo,
            replyToMessage: current.replyToMessage,
            images: images.length ? images : undefined,
            key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          };
    const store = useAgentChatStore.getState();
    store.setFollowLatest(conversationId, true);
    store.setBusy(conversationId, true);
    store.setError(conversationId, '');
    // 乐观 UI：本地先落一条 pending 回合，主人的话立刻出现在会话里。
    // 旧协议下服务端 POST 会阻塞到整轮生成结束才返回（W01 spec §3.2），等响应才上屏
    // 会让主人盯着空输入区等整轮生成；群聊协议立即返回 202、回复走 WS。
    // 本地这条与服务端结果按 requestId 对账。
    const attachments = current.attachments;
    const localTurn: LocalAgentTurn = {
      id: nextLocalTurnId(),
      requestId: pending.key,
      text: pending.text,
      speaker: pending.target === 'pet' ? 'pet' : 'assistant',
      readAt: null,
      target: pending.target,
      replyToMessage: pending.replyToMessage ?? null,
      reply: '',
      status: 'pending',
      createdAt: new Date().toISOString(),
      conversationId,
      replyTo: pending.replyTo ?? null,
      sources: [],
      blocks: null,
      images: attachments
        .filter((item) => item.status === 'done' && item.imageId)
        .map((item) => ({
          id: item.imageId as string,
          width: item.width,
          height: item.height,
          bytes: null,
        })),
      // 群聊回合带逐条回复：本地这条从空开始，路由后由服务端快照填上谁来说话
      ...(state.dualAgent ? { messages: [] } : {}),
      local: true,
    };
    store.applyLocalTurn(localTurn, conversationId);
    // 原话已进会话，输入区清空；发送失败时再回填（失败保留原话与图片）
    store.setDraft(conversationId, '');
    store.setReplyTo(conversationId, undefined);
    store.setReplyToMessage(conversationId, undefined);
    store.clearAttachments(conversationId);
    const transmit = async (): Promise<void> => {
      let request = pending;
      for (;;) {
        try {
          const result = await sendAgentTurn(
            request.pet,
            request.text,
            request.target,
            request.key,
            {
              conversationId: request.conversation,
              replyTo: request.replyTo,
              images: request.images,
              replyToMessage: request.replyToMessage,
            },
          );
          if (!currentSession()) return;
          learnDualAgent(result.protocol === AGENT_GROUP_PROTOCOL);
          const latest = useAgentChatStore.getState();
          latest.applyTurn(result.turn, result.conversation); // 同 requestId：本地乐观回合被服务端结果替换
          latest.setPending(request.conversation, null);
          // W03：聊天内可能直写了助手设定（如改名），拉齐让副标题/指向 pill/消息标签即时生效
          void useCompanionSettingsStore.getState().refresh();
          return;
        } catch (error) {
          if (!currentSession()) return;
          const latest = useAgentChatStore.getState();
          if (request.target === 'both' && isDualAgentUnavailable(error)) {
            // 账号已不在双 Agent 里（服务端在落库前拒绝）：同一个请求键按未指定重发，不会重复落回合
            learnDualAgent(false);
            request = { ...request, target: 'auto' };
            latest.setPending(request.conversation, request);
            latest.applyLocalTurn(
              { ...localTurn, target: 'auto', messages: undefined },
              request.conversation,
            );
            continue;
          }
          // 服务端没确认：撤下本地气泡，原话与图片回到它要发往的那个会话的输入区
          // （哪怕用户已经切到别的会话），可直接重试；pending 保留同键，服务端按 requestId 去重
          latest.removeLocalTurn(request.key);
          latest.setDraft(request.conversation, request.text);
          latest.setReplyTo(request.conversation, request.replyTo);
          latest.setReplyToMessage(request.conversation, request.replyToMessage);
          if (attachments.length) latest.addAttachments(request.conversation, attachments);
          latest.setError(request.conversation, '暂时没能确认送达，原话和图片已保留，可以重试');
          return;
        }
      }
    };
    try {
      await transmit();
    } finally {
      if (currentSession()) useAgentChatStore.getState().setBusy(pending.conversation, false);
    }
  }, []);

  /** 双 Agent 群聊：只重跑这一轮里失败的那条发言，成功的保留不动（服务端 409 时以拉齐结果为准） */
  const retryTurn = useCallback(
    async (turnId: number) => {
      const session = getToken();
      const currentSession = () => !!session && getToken() === session;
      const state = useAgentChatStore.getState();
      if (!state.petId) return;
      try {
        const turn = await retryAgentTurn(state.petId, turnId);
        if (!currentSession()) return;
        const latest = useAgentChatStore.getState();
        if (turn.conversationId !== null && latest.activeId === turn.conversationId) {
          latest.setFollowLatest(turn.conversationId, true);
        }
        latest.applyTurn(turn);
      } catch (error) {
        if (!currentSession()) return;
        const latest = useAgentChatStore.getState();
        const owner = conversationOfTurn(latest, turnId);
        if (error instanceof ApiError && error.status === 409) {
          // 没有可重试的发言 / 回合还在进行中 / 双 Agent 已停用：界面按服务端当前状态刷新
          if (owner !== null) void loadConversation(owner, true);
          return;
        }
        if (owner !== null) latest.setError(owner, '暂时没能重新发送，稍后再试');
      }
    },
    [loadConversation],
  );

  const loadOlder = useCallback(async () => {
    const session = getToken();
    const currentSession = () => !!session && getToken() === session;
    const state = useAgentChatStore.getState();
    const id = state.activeId;
    const current = id === null ? undefined : state.chats[id];
    if (!state.petId || id === null || !current || !current.turns.length || !current.hasMore)
      return;
    try {
      const data = await fetchAgentTurns(state.petId, id, current.turns[0].id);
      if (!currentSession()) return;
      useAgentChatStore.getState().prependOlder(id, data.turns);
    } catch {
      if (currentSession() && id !== null) {
        useAgentChatStore.getState().setError(id, '更早的聊天暂时没取回来');
      }
    }
  }, []);

  /** 以下 setter 只作用于当前会话 */
  const setDraft = useCallback((value: string) => {
    const id = useAgentChatStore.getState().activeId;
    if (id !== null) useAgentChatStore.getState().setDraft(id, value);
  }, []);
  const setTarget = useCallback((value: AgentTarget) => {
    const id = useAgentChatStore.getState().activeId;
    if (id !== null) useAgentChatStore.getState().setTarget(id, value);
  }, []);
  const setReplyTo = useCallback((value: number | undefined) => {
    const id = useAgentChatStore.getState().activeId;
    if (id !== null) useAgentChatStore.getState().setReplyTo(id, value);
  }, []);
  const setReplyToMessage = useCallback((value: string | undefined) => {
    const id = useAgentChatStore.getState().activeId;
    if (id !== null) useAgentChatStore.getState().setReplyToMessage(id, value);
  }, []);
  const setScrollY = useCallback((value: number) => {
    const id = useAgentChatStore.getState().activeId;
    if (id !== null) useAgentChatStore.getState().setScrollY(id, value);
  }, []);
  const setFollowLatest = useCallback((value: boolean) => {
    const id = useAgentChatStore.getState().activeId;
    if (id !== null) useAgentChatStore.getState().setFollowLatest(id, value);
  }, []);

  return {
    learningDays,
    soundDays,
    learningState,
    soundState,
    activeId,
    chat,
    conversation,
    /** 当前会话的回合还没取回来（正在取，或取失败了还没重试） */
    unloaded: own && activeId !== null && chat.loadedFor === null,
    loading: loadingId === activeId,
    setDraft,
    setTarget,
    setReplyTo,
    setReplyToMessage,
    setScrollY,
    setFollowLatest,
    openConversation,
    switchConversation,
    reload,
    loadConversation,
    refreshList,
    markRead,
    send,
    pickImages,
    retryAttachment,
    removeAttachment,
    loadOlder,
    retryTurn,
  };
}

export type AgentChat = ReturnType<typeof useAgentChat>;
