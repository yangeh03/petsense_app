import { useCallback, useState, useSyncExternalStore } from 'react';
import { AppState } from 'react-native';
import { useFocusEffect } from 'expo-router';
import { ApiError, fetchTrends } from '@/services/api';
import { TrendQuery } from '@/services/trend-query';
import type { TrendPeriod } from '@/types/social';

export function useTrends(
  userId: number | undefined,
  deviceId: string | null | undefined,
  period: TrendPeriod,
  expanded: boolean,
) {
  const [query] = useState(() => new TrendQuery());
  const key = `${userId ?? 'guest'}/${deviceId ?? 'none'}/${period}`;
  const snapshot = useSyncExternalStore(query.subscribe, query.getSnapshot, query.getSnapshot);
  const refresh = useCallback(async () => {
    if (!userId) {
      query.reset(key);
      return;
    }
    await query.refresh(key, async () => {
      let data;
      try {
        data = await fetchTrends(period);
      } catch (error) {
        throw new Error(
          error instanceof ApiError ? error.message : '网络连接失败，请检查网络后重试',
        );
      }
      if (data.schemaVersion !== 2) throw new Error('需要更新服务端以查看完整趋势统计');
      return data;
    });
  }, [query, key, userId, period]);
  useFocusEffect(
    useCallback(() => {
      if (!userId) {
        query.reset(key);
        return;
      }
      if (query.getSnapshot().key !== key) query.reset(key);
      const load = () => {
        if (expanded && AppState.currentState === 'active' && !query.getSnapshot().loading)
          void refresh();
      };
      load();
      const subscription = AppState.addEventListener('change', (state) => {
        if (state === 'active') load();
        else query.cancel();
      });
      const timer = expanded ? setInterval(load, 60_000) : null;
      return () => {
        if (timer) clearInterval(timer);
        subscription.remove();
        query.cancel();
      };
    }, [query, key, userId, expanded, refresh]),
  );
  return {
    data: snapshot.key === key ? snapshot.data : null,
    loading: snapshot.key === key ? snapshot.loading : Boolean(userId),
    error: snapshot.key === key ? snapshot.error : '',
    refresh,
  };
}
