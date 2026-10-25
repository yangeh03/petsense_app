import { useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';

import { useAuth } from '@/hooks/use-auth';
import { fetchDashboard } from '@/services/api';
import { fetchObservations } from '@/services/observations';
import type { ObservationPeriod, Observations } from '@/types/observations';
import type { DashboardData } from '@/types/social';

type Result = {
  userId: number;
  dashboard: DashboardData | null;
  observations: Observations | null;
};

/** Scope cached data to the signed-in user; refresh profile, history and current snapshot together. */
export function useObservations(period: ObservationPeriod) {
  const { user } = useAuth();
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    if (!user) return;
    const requestId = ++sequence.current;
    setBusy(true);
    const [dashboard, observations] = await Promise.allSettled([
      fetchDashboard(),
      fetchObservations(period),
    ]);
    if (requestId !== sequence.current) return;
    setResult((old) => ({
      userId: user.id,
      dashboard:
        dashboard.status === 'fulfilled'
          ? dashboard.value
          : old?.userId === user.id
            ? old.dashboard
            : null,
      observations:
        observations.status === 'fulfilled'
          ? observations.value
          : old?.userId === user.id
            ? old.observations
            : null,
    }));
    setError(
      dashboard.status === 'rejected' || observations.status === 'rejected'
        ? '部分数据未能更新，已保留最近记录。请重试。'
        : '',
    );
    setBusy(false);
  }, [user, period]);
  useFocusEffect(
    useCallback(() => {
      void load();
      const timer = setInterval(() => void load(), 15_000);
      return () => {
        clearInterval(timer);
        sequence.current += 1;
      };
    }, [load]),
  );
  const scoped = result?.userId === user?.id ? result : null;
  return {
    dashboard: scoped?.dashboard ?? null,
    observations: scoped?.observations ?? null,
    error: scoped ? error : '',
    busy,
    reload: load,
  };
}
