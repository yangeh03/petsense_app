/**
 * W12 收件箱 Hook：铃铛、今日预览与收件箱页共用同一份服务端列表、计数与已读语义。
 *
 * 服务端是唯一事实源：本地不缓存未读数，失败按「计数未知」处理（不显示角标），
 * 避免与服务端计数漂移。轮询间隔 60 秒，回到前台时立即刷新。
 */

import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';

import {
  fetchInbox,
  markAllInboxRead,
  markInboxRead,
  type InboxCapability,
  type InboxItem,
  type InboxListing,
} from '@/services/agent-inbox';

const POLL_MS = 60_000;

async function loadListing(petId?: number): Promise<InboxListing> {
  return fetchInbox(petId == null ? {} : { pet: petId });
}

export function useAgentInbox(petId?: number) {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [preview, setPreview] = useState<InboxItem | null>(null);
  const [capability, setCapability] = useState<InboxCapability | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const apply = useCallback((data: InboxListing) => {
    setItems(data.items);
    setUnreadCount(data.unreadCount);
    setPreview(data.preview);
    setCapability(data.capability);
    setError('');
    setLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await loadListing(petId));
    } catch {
      setError('暂时读不到收件箱');
      setLoading(false);
    }
  }, [apply, petId]);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      loadListing(petId)
        .then((data) => {
          if (!cancelled) apply(data);
        })
        .catch(() => {
          if (cancelled) return;
          setError('暂时读不到收件箱');
          setLoading(false);
        });
    };
    load();
    const timer = setInterval(load, POLL_MS);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') load();
    });
    return () => {
      cancelled = true;
      clearInterval(timer);
      subscription.remove();
    };
  }, [apply, petId]);

  const markRead = useCallback(async (id: number) => {
    try {
      const data = await markInboxRead(id);
      setUnreadCount(data.unreadCount);
      setItems((current) =>
        current.map((item) => (item.id === id ? { ...item, readAt: data.item.readAt } : item)),
      );
      setPreview((current) => (current && current.id === id ? null : current));
    } catch {
      setError('标记已读失败');
    }
  }, []);

  const markAllRead = useCallback(async () => {
    try {
      await markAllInboxRead(items[0]?.id);
      await refresh();
    } catch {
      setError('全部已读失败');
    }
  }, [items, refresh]);

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
