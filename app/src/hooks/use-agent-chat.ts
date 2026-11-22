/**
 * 伙伴会话生命周期 Hook（W01，agent-w01-spec.md §4.2/§4.4；W07 图片附件）：
 * - WS 直连为主、断线降级轮询（行为与旧版一致）；
 * - AsyncStorage 按用户持久化草稿/主题/滚动/最近回合（替换旧模块级内存 Map）；
 * - 发送乐观 UI + 同键重试去重；失败保留原话与图片；
 * - W07：相册/拍照选取 → 压缩 → 逐张上传（失败可重试），发送引用服务端 imageId。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as ImagePicker from 'expo-image-picker';
import { agentChannel } from '@/services/agent-channel';
import {
  fetchAgentObservation,
  fetchAgentTurns,
  markAgentRead,
  sendAgentTurn,
  uploadAgentImage,
  type AgentTurn,
} from '@/services/agent';
import { ApiError, fetchDashboard } from '@/services/api';
import {
  AGENT_JPEG_QUALITY,
  AGENT_UPLOAD_MAX_EDGE,
  normalizeForUpload,
} from '@/services/normalize-image';
import { useAgentChatStore, type AgentAttachment } from '@/stores/agent-chat';

const STORAGE_PREFIX = 'petsense.agent.chat.';
const POLL_INTERVAL_MS = 800;
const KEEP_TURNS = 20;
const MAX_ATTACHMENTS = 3;

type Snapshot = {
  draft: string;
  target: 'auto' | 'pet' | 'assistant';
  topic?: number;
  replyTo?: number;
  scrollY: number;
  turns: AgentTurn[];
  attachments?: AgentAttachment[];
  pending: {
    text: string;
    target: 'auto' | 'pet' | 'assistant';
    pet: number;
    key: string;
    topic?: number;
    replyTo?: number;
    images?: string[];
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
        setSoundDays(
          facts.learningDimensions.find((value) => value.dimension === 'sounds')?.validDays ?? 0,
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
      // 快照恢复：上传中的附件随进程中断，标记失败等待重试或移除
      if (snapshot.attachments?.length) {
        current.addAttachments(
          snapshot.attachments.map((item) =>
            item.status === 'uploading' ? { ...item, status: 'failed' as const } : item,
          ),
        );
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
        attachments: state.attachments,
        pending: state.pending,
      };
      void AsyncStorage.setItem(STORAGE_PREFIX + userId, JSON.stringify(snapshot)).catch(() => {});
    }, 400);
    return () => clearTimeout(timer);
  }, [
    userId,
    store.draft,
    store.target,
    store.topic,
    store.replyTo,
    store.turns,
    store.pending,
    store.attachments,
  ]);

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

  /** W07：单张上传（选中即传，失败标红可重试；服务端内容寻址，重试零成本） */
  const uploadOne = useCallback(async (attachment: AgentAttachment) => {
    const state = useAgentChatStore.getState();
    if (!state.petId || !attachment.localUri) {
      useAgentChatStore.getState().updateAttachment(attachment.key, { status: 'failed' });
      return;
    }
    try {
      const meta = await uploadAgentImage(state.petId, {
        uri: attachment.localUri,
        name: 'photo.jpg',
        type: 'image/jpeg',
      });
      useAgentChatStore.getState().updateAttachment(attachment.key, {
        imageId: meta.id,
        width: meta.width ?? attachment.width,
        height: meta.height ?? attachment.height,
        status: 'done',
        failReason: undefined,
      });
    } catch (error) {
      // 服务端结构化消息如实透出（如超配额「请先清理历史图片」），网络失败用通用文案
      const reason = error instanceof ApiError ? error.message : '图片上传失败，请检查网络后重试';
      useAgentChatStore
        .getState()
        .updateAttachment(attachment.key, { status: 'failed', failReason: reason });
    }
  }, []);

  /** W07：相册（多选）/拍照（单张）→ 压缩（1280/q0.8，D2）→ 逐张上传 */
  const pickImages = useCallback(
    async (source: 'library' | 'camera') => {
      const state = useAgentChatStore.getState();
      if (!state.petId) return;
      const limit = MAX_ATTACHMENTS - state.attachments.length;
      if (limit <= 0) return;
      const options: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.8 };
      const result =
        source === 'library'
          ? await ImagePicker.launchImageLibraryAsync({
              ...options,
              allowsMultipleSelection: true,
              selectionLimit: limit,
            })
          : await ImagePicker.launchCameraAsync(options);
      if (result.canceled) return;
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
        const attachment: AgentAttachment = {
          key,
          localUri: normalized.uri,
          width: asset.width ?? 0,
          height: asset.height ?? 0,
          imageId: null,
          status: 'uploading',
        };
        useAgentChatStore.getState().addAttachments([attachment]);
        void uploadOne(attachment);
      }
    },
    [uploadOne],
  );

  const retryAttachment = useCallback(
    (key: string) => {
      const attachment = useAgentChatStore.getState().attachments.find((item) => item.key === key);
      if (!attachment || attachment.status === 'uploading') return;
      useAgentChatStore.getState().updateAttachment(key, { status: 'uploading' });
      void uploadOne({ ...attachment, status: 'uploading' });
    },
    [uploadOne],
  );

  const removeAttachment = useCallback((key: string) => {
    useAgentChatStore.getState().removeAttachment(key);
  }, []);

  const send = useCallback(async () => {
    const state = useAgentChatStore.getState();
    const draft = state.draft.trim();
    if (!state.petId || state.busy) return;
    // 未全完成不可发（含失败，spec §4.1）：失败图留在预览条等重试或移除，不随消息发出也不被清掉
    if (state.attachments.some((item) => item.status !== 'done')) return;
    const images = state.attachments
      .filter((item) => item.status === 'done' && item.imageId)
      .map((item) => item.imageId as string);
    if (!draft && !images.length) return;
    const sameContent =
      state.pending &&
      state.pending.text === draft &&
      state.pending.target === state.target &&
      state.pending.pet === state.petId &&
      state.pending.topic === state.topic &&
      state.pending.replyTo === state.replyTo &&
      (state.pending.images ?? []).join(',') === images.join(',');
    const pending =
      sameContent && state.pending
        ? state.pending
        : {
            text: draft,
            target: state.target,
            pet: state.petId,
            topic: state.topic,
            replyTo: state.replyTo,
            images: images.length ? images : undefined,
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
        pending.images,
      );
      const current = useAgentChatStore.getState();
      current.applyTurn(result.turn);
      current.setPending(null);
      current.setDraft('');
      current.setReplyTo(undefined);
      current.clearAttachments();
    } catch {
      useAgentChatStore.getState().setError('暂时没能确认送达，原话和图片已保留，可以重试');
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
    followReply,
    setFollowReply,
    reload,
    send,
    pickImages,
    retryAttachment,
    removeAttachment,
    loadOlder,
    markRead,
  };
}
