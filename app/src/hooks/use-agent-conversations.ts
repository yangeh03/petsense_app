/**
 * 会话列表与增删改（W16，docs/agent-w16-spec.md §6.3）。
 *
 * 结果带账号标签：listFor 是拉到这份列表的账号 id，渲染时推导——退出或换账号后上一账号的列表
 * 立即消失；晚到的旧账号响应按标签丢弃（写法同 use-agent-inbox）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createAgentConversation,
  deleteAgentConversation,
  fetchAgentConversations,
  renameAgentConversation,
  type AgentConversation,
} from '@/services/agent';
import { ApiError, getToken } from '@/services/api';
import { useAgentChatStore } from '@/stores/agent-chat';

/** 删除的结果：失败时 message 是可直接展示的原因，busy 表示会话里还有进行中的委托 */
export type RemoveConversationResult = { ok: true } | { ok: false; message: string; busy: boolean };

/** 拉会话列表并写入 store。调用方负责账号标签校验与错误处理。 */
export async function fetchConversationList(petId: number, ownerId: number) {
  const data = await fetchAgentConversations(petId);
  useAgentChatStore.getState().setList(data.conversations, ownerId);
  return data;
}

export function useAgentConversations(user: { id: number } | null, petId: number | null) {
  const ownerId = user?.id ?? null;
  const list = useAgentChatStore((state) => state.list);
  const listFor = useAgentChatStore((state) => state.listFor);
  const listStale = useAgentChatStore((state) => state.listStale);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const ownerRef = useRef(ownerId);
  useEffect(() => {
    ownerRef.current = ownerId;
  }, [ownerId]);

  /** 发请求前调用，返回「这个请求的响应现在还算不算数」：账号没换、会话令牌没变 */
  const guard = useCallback(() => {
    const session = getToken();
    const requestOwner = ownerId;
    return () => ownerRef.current === requestOwner && !!session && getToken() === session;
  }, [ownerId]);

  const refresh = useCallback(async () => {
    if (petId === null || ownerId === null) return;
    const valid = guard();
    setLoading(true);
    try {
      const data = await fetchConversationList(petId, ownerId);
      if (!valid()) return;
      setHasMore(data.hasMore);
      setError('');
    } catch {
      if (valid()) setError('暂时没能取回会话列表，点这里重试');
    } finally {
      if (valid()) setLoading(false);
    }
  }, [petId, ownerId, guard]);

  useEffect(() => {
    void Promise.resolve().then(refresh); // 不在 effect 里同步触发状态更新
  }, [refresh]);

  const loadMore = useCallback(async () => {
    const state = useAgentChatStore.getState();
    if (petId === null || ownerId === null || state.listFor !== ownerId || loading) return;
    const valid = guard();
    try {
      const data = await fetchAgentConversations(petId, { offset: state.list.length });
      if (!valid()) return;
      const known = new Set(state.list.map((item) => item.id));
      useAgentChatStore
        .getState()
        .setList(
          [...state.list, ...data.conversations.filter((item) => !known.has(item.id))],
          ownerId,
        );
      setHasMore(data.hasMore);
    } catch {
      // 翻页失败不打扰：下次刷新列表时一并补齐
    }
  }, [petId, ownerId, loading, guard]);

  const create = useCallback(async (): Promise<AgentConversation | null> => {
    if (petId === null || ownerId === null) return null;
    const valid = guard();
    setBusy(true);
    try {
      const data = await createAgentConversation(petId);
      if (!valid()) return null;
      useAgentChatStore.getState().upsertConversations(data.conversation);
      return data.conversation;
    } catch {
      if (valid()) setError('没能新建会话，请稍后再试');
      return null;
    } finally {
      if (valid()) setBusy(false);
    }
  }, [petId, ownerId, guard]);

  /** 改名：失败时返回可直接展示的原因，成功返回空 */
  const rename = useCallback(
    async (conversationId: number, title: string): Promise<string | null> => {
      if (ownerId === null) return '还没登录';
      const valid = guard();
      setBusy(true);
      try {
        const data = await renameAgentConversation(conversationId, title);
        if (!valid()) return null;
        useAgentChatStore.getState().upsertConversations(data.conversation);
        return null;
      } catch (error) {
        if (!valid()) return null;
        if (error instanceof ApiError && error.message) return error.message;
        return '没能保存新的名字，请稍后再试';
      } finally {
        if (valid()) setBusy(false);
      }
    },
    [ownerId, guard],
  );

  /** 删除：被服务端拒绝（进行中的委托或未结束的提醒）时带回原因 */
  const remove = useCallback(
    async (conversationId: number): Promise<RemoveConversationResult> => {
      if (ownerId === null) return { ok: false, message: '还没登录', busy: false };
      const valid = guard();
      setBusy(true);
      try {
        await deleteAgentConversation(conversationId);
        if (valid()) useAgentChatStore.getState().dropConversation(conversationId);
        return { ok: true };
      } catch (error) {
        if (!valid()) return { ok: false, message: '', busy: false };
        const payload =
          error instanceof ApiError
            ? (error.payload as Record<string, unknown> | undefined)
            : undefined;
        const rejected = error instanceof ApiError && error.status === 409;
        return {
          ok: false,
          message:
            (typeof payload?.message === 'string' && payload.message) ||
            (rejected ? '这个会话现在还不能删除' : '没能删除，请稍后再试'),
          busy: rejected && Number(payload?.activeTasks ?? 0) > 0,
        };
      } finally {
        if (valid()) setBusy(false);
      }
    },
    [ownerId, guard],
  );

  // 账号标签在渲染时推导：换了账号而 store 还没重置的这一帧，不展示上一个账号的列表
  const own = ownerId !== null && listFor === ownerId;
  return {
    list: own ? list : [],
    loaded: own,
    loading,
    error,
    busy,
    hasMore,
    stale: listStale,
    refresh,
    loadMore,
    create,
    rename,
    remove,
  };
}
