/**
 * W12 收件箱 Hook：铃铛、今日预览与收件箱页共用同一份服务端列表、计数与已读语义。
 *
 * 服务端是唯一事实源：本地不缓存未读数，失败按「计数未知」处理（不显示角标），
 * 避免与服务端计数漂移。轮询间隔 60 秒，回到前台时立即刷新。
 *
 * 结果带着账号标签：退出或换账号后，上一账号的列表、计数与预览立即不再展示，
 * 晚到的旧账号响应按标签丢弃。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppState } from 'react-native';

import {
  fetchInbox,
  markAllInboxRead,
  markInboxRead,
  type InboxListing,
} from '@/services/agent-inbox';
import { useAuth } from '@/hooks/use-auth';

const POLL_MS = 60_000;

async function loadListing(petId?: number): Promise<InboxListing> {
  return fetchInbox(petId == null ? {} : { pet: petId });
}

/** 一份属于某个账号的最新结果；listing 为 null 表示还没读到过（或读取失败且无旧列表）。 */
type Snapshot = { owner: number; listing: InboxListing | null; error: string };

export function useAgentInbox(petId?: number) {
  const { user } = useAuth();
  const ownerId = user?.id ?? null;
  const ownerRef = useRef<number | null>(ownerId);
  useEffect(() => {
    ownerRef.current = ownerId;
  }, [ownerId]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);

  // 只采用当前账号的结果：退出或换账号后推导值立即回到初始态
  const own = ownerId != null && snapshot?.owner === ownerId ? snapshot : null;
  const listing = own?.listing ?? null;
  const items = useMemo(() => listing?.items ?? [], [listing]);
  const unreadCount = listing?.unreadCount ?? 0;
  const preview = listing?.preview ?? null;
  const capability = listing?.capability ?? null;
  const loading = ownerId != null && !own;
  const error = own?.error ?? '';

  const refresh = useCallback(async () => {
    const owner = ownerId;
    if (owner == null) return;
    try {
      const data = await loadListing(petId);
      // 请求期间退出或换了账号：上一账号的响应不进界面
      if (ownerRef.current !== owner) return;
      setSnapshot({ owner, listing: data, error: '' });
    } catch {
      if (ownerRef.current !== owner) return;
      setSnapshot((current) => ({
        owner,
        listing: current?.owner === owner ? current.listing : null,
        error: '暂时读不到收件箱',
      }));
    }
  }, [ownerId, petId]);

  useEffect(() => {
    if (ownerId == null) return; // 未登录：不拉取，推导值保持初始态
    const load = () => {
      void refresh(); // refresh 自行捕获异常，按账号标签决定是否进界面
    };
    load();
    const timer = setInterval(load, POLL_MS);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') load();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [ownerId, refresh]);

  const failWith = useCallback((message: string) => {
    setSnapshot((current) => (current ? { ...current, error: message } : current));
  }, []);

  const markRead = useCallback(
    async (id: number) => {
      const owner = ownerId;
      try {
        const data = await markInboxRead(id);
        setSnapshot((current) => {
          if (ownerRef.current !== owner || current?.owner !== owner || !current.listing) {
            return current;
          }
          const next = current.listing;
          return {
            ...current,
            listing: {
              ...next,
              unreadCount: data.unreadCount,
              items: next.items.map((item) =>
                item.id === id ? { ...item, readAt: data.item.readAt } : item,
              ),
              preview: next.preview && next.preview.id === id ? null : next.preview,
            },
          };
        });
      } catch {
        if (ownerRef.current === owner) failWith('标记已读失败');
      }
    },
    [ownerId, failWith],
  );

  const markAllRead = useCallback(async () => {
    try {
      await markAllInboxRead(items[0]?.id);
      await refresh();
    } catch {
      failWith('全部已读失败');
    }
  }, [items, refresh, failWith]);

  return {
    items,
    unreadCount,
    preview,
    capability,
    loading,
    error,
    refresh,
    markRead,
    markAllRead,
  };
}
