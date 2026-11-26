import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';

import { useAuth } from '@/hooks/use-auth';
import { ApiError, request } from '@/services/api';
import { displayPlan, type DisplayPlan } from '@/services/display-plan';

export function useDisplayPlan(deviceId: string | undefined) {
  const { user } = useAuth();
  const [revision, setRevision] = useState(0);
  const scope = user && deviceId ? `${user.id}:${deviceId}:${revision}` : '';
  const [result, setResult] = useState<{
    scope: string;
    plan: DisplayPlan | null;
    error: string;
  } | null>(null);
  useFocusEffect(
    useCallback(() => {
      if (!scope || !deviceId) return;
      let active = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 12000);
      void request<{ deviceId: string; plan: unknown }>(
        `/uwb/plan?deviceId=${encodeURIComponent(deviceId)}`,
        { signal: controller.signal },
      )
        .then((data) => {
          if (!active) return;
          if (data.deviceId !== deviceId) throw new Error('底图设备不匹配');
          const plan = displayPlan(data.plan, deviceId);
          if (data.plan !== null && !plan) throw new Error('底图数据不完整');
          setResult({ scope, plan, error: '' });
        })
        .catch((failure) => {
          if (!active) return;
          // Older servers have no display-plan endpoint; keep their calibrated-map flow.
          const absent = failure instanceof ApiError && failure.status === 404;
          setResult({ scope, plan: null, error: absent ? '' : '场地底图加载失败，请重试' });
        })
        .finally(() => clearTimeout(timeout));
      return () => {
        active = false;
        controller.abort();
        clearTimeout(timeout);
      };
    }, [scope, deviceId]),
  );
  const scoped = scope && result?.scope === scope ? result : null;
  return {
    plan: scoped?.plan ?? null,
    loading: !!scope && !scoped,
    error: scoped?.error ?? '',
    reload: () => setRevision((r) => r + 1),
  };
}
