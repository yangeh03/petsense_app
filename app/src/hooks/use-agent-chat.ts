/**
 * 伙伴会话生命周期 Hook（W01，agent-w01-spec.md §4.2/§4.4）：
 * - WS 直连为主、断线降级轮询（行为与旧版一致）；
 * - AsyncStorage 按用户持久化草稿/主题/滚动/最近回合（替换旧模块级内存 Map）；
 * - 发送乐观 UI + 同键重试去重；失败保留原话。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { agentChannel } from '@/services/agent-channel';
import {
  fetchAgentObservation,
  fetchAgentTurns,
  markAgentRead,
  sendAgentTurn,
  type AgentTurn,
} from '@/services/agent';
import { fetchDashboard } from '@/services/api';
import { useAgentChatStore } from '@/stores/agent-chat';

const STORAGE_PREFIX = 'petsense.agent.chat.';
const POLL_INTERVAL_MS = 800;
const KEEP_TURNS = 20;

type Snapshot = {
  draft: string;
  target: 'auto' | 'pet' | 'assistant';
  topic?: number;
  replyTo?: number;
  scrollY: number;
  turns: AgentTurn[];
  pending: {
    text: string;
    target: 'auto' | 'pet' | 'assistant';
    pet: number;
    key: string;
    topic?: number;
    replyTo?: number;
  } | null;
};

async function loadSnapshot(userId: number): Promise<Snapshot | null> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_PREFIX + userId);
    return raw ? (JSON.parse(raw) as Snapshot) : null;
  } catch {
    return null;
  }
}

export function useAgentChat(user: { id: number } | null) {
  const userId = user?.id ?? null;
  const store = useAgentChatStore();
  const [learningDays, setLearningDays] = useState<number | null>(null);
  const [soundDays, setSoundDays] = useState(0);
  const [learningState, setLearningState] = useState('learning');
  const [soundState, setSoundState] = useState('learning');
  const [followReply, setFollowReply] = useState(false);
  const restoredFor = useRef<number | null>(null);
  const wasOpen = useRef(false);

  const reload = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (!userId) {
      state.resetForUser();
      setLearningDays(null);
      return;
    }
    try {
      const data = await fetchDashboard();
      state.setPet(data.pet);
      if (data.pet) {
        const latest = (await fetchAgentTurns(data.pet.id)).turns;
        useAgentChatStore
          .getState()
          .setTurns([
            ...useAgentChatStore
              .getState()
              .turns.filter((turn) => latest.length > 0 && turn.id < latest[0].id),
            ...latest,
          ]);
        const end = new Date();
        const start = new Date(end.getTime() - 89 * 86400000);
        const facts = await fetchAgentObservation(
          data.pet.id,
          start.toISOString(),
          end.toISOString(),
        );
        setLearningDays(facts.learning.validDays);
        setLearningState(facts.learning.state);
        setSoundDays(
          facts.learningDimensions.find((value) => value.dimension === 'sounds')?.validDays ?? 0,
        );
        setSoundState(
          facts.learningDimensions.find((value) => value.dimension === 'sounds')?.state ??
            'learning',
        );
      }
      useAgentChatStore.getState().setLoadedFor(userId);
      useAgentChatStore.getState().setError('');
    } catch {
      useAgentChatStore.getState().setError('暂时没能取回我们的聊天，点这里重试');
    }
  }, [userId]);

  /** 用户切换：先恢复本地快照（首屏不空白），再拉服务端 */
  useEffect(() => {
    if (restoredFor.current === userId) return;
    restoredFor.current = userId;
    wasOpen.current = false;
    const state = useAgentChatStore.getState();
    state.resetForUser();
    if (!userId) return;
    let active = true;
    void loadSnapshot(userId).then((snapshot) => {
      if (!active || !snapshot) return;
      const current = useAgentChatStore.getState();
      current.setDraft(snapshot.draft ?? '');
      current.setTarget(snapshot.target ?? 'auto');
      if (snapshot.topic !== undefined) current.setTopic(snapshot.topic);
      current.setReplyTo(snapshot.replyTo);
      current.setScrollY(snapshot.scrollY ?? 0);
      if (snapshot.turns?.length) {
        current.setTurns(snapshot.turns);
        current.setLoadedFor(userId);
      }
      if (snapshot.pending) current.setPending(snapshot.pending);
    });
    return () => {
      active = false;
    };
  }, [userId]);

  useEffect(() => {
    void Promise.resolve().then(reload);
  }, [reload]);

  /** 本地快照持久化（轻量防抖） */
  useEffect(() => {
    if (!userId) return;
    const timer = setTimeout(() => {
      const state = useAgentChatStore.getState();
      const snapshot: Snapshot = {
        draft: state.draft,
        target: state.target,
        topic: state.topic,
        replyTo: state.replyTo,
        scrollY: state.scrollY,
        turns: state.turns.slice(-KEEP_TURNS),
        pending: state.pending,
      };
      void AsyncStorage.setItem(STORAGE_PREFIX + userId, JSON.stringify(snapshot)).catch(() => {});
    }, 400);
    return () => clearTimeout(timer);
  }, [userId, store.draft, store.target, store.topic, store.replyTo, store.turns, store.pending]);

  /** WS：状态 + 帧 + 重连拉齐（以服务端已保存结果为准） */
  useEffect(() => {
    if (!userId) return;
    const unsubscribeStatus = agentChannel.subscribeStatus((status) => {
      useAgentChatStore.getState().setConnection(status);
      if (status === 'open') {
        if (wasOpen.current || useAgentChatStore.getState().turns.length) {
          void fetchAgentTurns(useAgentChatStore.getState().petId ?? -1)
            .then((data) => useAgentChatStore.getState().setTurns(data.turns))
            .catch(() => {});
        }
        wasOpen.current = true;
      }
    });
    const unsubscribeFrames = agentChannel.subscribe((frame) => {
      if (frame.type === 'turn') {
        useAgentChatStore.getState().applyTurn(frame.turn);
      } else if (frame.type === 'delta') {
        useAgentChatStore
          .getState()
          .applyDelta(frame.petId, frame.turnId, frame.text, frame.speaker);
      }
    });
    agentChannel.reconnect();
    return () => {
      unsubscribeStatus();
      unsubscribeFrames();
    };
  }, [userId]);

  /** 降级轮询：仅在无 WS 连接且有待完成回合时工作（沿用旧行为） */
  useEffect(() => {
    const generating = store.busy || store.turns.some((turn) => turn.status === 'pending');
    if (!userId || !store.petId || !generating || store.connection === 'open') return;
    let active = true;
    const timer = setInterval(() => {
      void fetchAgentTurns(store.petId as number)
        .then((data) => {
          if (active) useAgentChatStore.getState().setTurns(data.turns);
        })
        .catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [userId, store.petId, store.busy, store.turns, store.connection]);

  const send = useCallback(async () => {
    const state = useAgentChatStore.getState();
    const draft = state.draft.trim();
    if (!state.petId || !draft || state.busy) return;
    const sameContent =
      state.pending &&
      state.pending.text === draft &&
      state.pending.target === state.target &&
      state.pending.pet === state.petId &&
      state.pending.topic === state.topic &&
      state.pending.replyTo === state.replyTo;
    const pending =
      sameContent && state.pending
        ? state.pending
        : {
            text: draft,
            target: state.target,
            pet: state.petId,
            topic: state.topic,
            replyTo: state.replyTo,
            key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          };
    setFollowReply(true);
    state.setBusy(true);
    state.setError('');
    try {
      const result = await sendAgentTurn(
        pending.pet,
        pending.text,
        pending.target,
        pending.key,
        pending.topic,
        pending.replyTo,
      );
      const current = useAgentChatStore.getState();
      current.applyTurn(result.turn);
      current.setPending(null);
      current.setDraft('');
      current.setReplyTo(undefined);
    } catch {
      useAgentChatStore.getState().setError('暂时没能确认送达，原话已保留，可以重试');
    } finally {
      useAgentChatStore.getState().setBusy(false);
    }
  }, []);

  const loadOlder = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (!state.petId || !state.turns.length) return;
    try {
      const data = await fetchAgentTurns(state.petId, state.turns[0].id);
      useAgentChatStore.getState().prependOlder(data.turns);
    } catch {
      useAgentChatStore.getState().setError('更早的聊天暂时没取回来');
    }
  }, []);

  const markRead = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (!state.petId || state.loadedFor !== userId) return;
    const ids = state.turns
      .filter(
        (turn) =>
          turn.status !== 'pending' &&
          !turn.readAt &&
          (state.topic === undefined || (turn.topicId ?? turn.id) === state.topic),
      )
      .slice(0, 40)
      .map((turn) => turn.id);
    if (!ids.length) return;
    try {
      const result = await markAgentRead(state.petId, ids);
      result.turns.forEach((turn) => useAgentChatStore.getState().applyTurn(turn));
    } catch {
      // 下次聚焦重试
    }
  }, [userId]);

  return {
    learningDays,
    soundDays,
    learningState,
    soundState,
    followReply,
    setFollowReply,
    reload,
    send,
    loadOlder,
    markRead,
  };
}
