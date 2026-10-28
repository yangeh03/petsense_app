import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';

import { useAuth } from '@/hooks/use-auth';
import { ApiError, request } from '@/services/api';
import { MapRefresh } from '@/services/map-refresh';
import { appendMeasuredTrail, positionMatchesMap } from '@/services/map-viewport';
import type { UwbMap, UwbPosition } from '@/types/uwb';

type Result = {
  scope: string;
  map: UwbMap | null;
  position: UwbPosition | null;
  trail: { x: number; y: number }[];
  lastMeasurement: string;
  deadline: number;
};

/** Authenticated snapshots expire locally even if the device/network goes quiet. */
export function usePositionMap(deviceId: string | undefined) {
  const { user } = useAuth();
  const [revision, setRevision] = useState(0);
  const scope = user && deviceId ? `${user.id}:${deviceId}:${revision}` : '';
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const [settledScope, setSettledScope] = useState('');
  useFocusEffect(
    useCallback(() => {
      if (!scope || !deviceId) return;
      let cancelled = false;
      let busy = false;
      const maps = new MapRefresh(request);
      let controller: AbortController | null = null;
      async function poll() {
        if (busy) return;
        busy = true;
        controller = new AbortController();
        const timeout = setTimeout(() => controller?.abort(), 8000);
        const query = `?deviceId=${encodeURIComponent(deviceId!)}`;
        try {
          const response = await request<{ deviceId: string; position: UwbPosition | null }>(
            `/uwb/position${query}`,
            { signal: controller.signal },
          );
          if (!response || response.deviceId !== deviceId)
            throw new Error('设备数据不完整或不匹配');
          const positionReceived = Date.now();
          const position = response.position;
          const map = await maps.refresh(deviceId!, position, controller.signal, Date.now());
          if (cancelled) return;
          const received = Date.now();
          setNow(received);
          setError('');
          setResult((old) => {
            const same =
              old?.scope === scope &&
              old.map?.mapId === map?.mapId &&
              old.map?.version === map?.version &&
              old.map?.revision === map?.revision;
            const remaining = Math.max(
              0,
              (position?.remainingMs ?? 0) - (received - positionReceived),
            );
            const valid = positionMatchesMap(position, map)
              ? { ...position!, remainingMs: remaining, fresh: position!.fresh && remaining > 0 }
              : null;
            const history = appendMeasuredTrail(
              same ? old.trail : [],
              valid,
              same ? old.lastMeasurement : '',
            );
            return {
              scope,
              map,
              position: valid,
              ...history,
              deadline: received + (valid?.remainingMs ?? 0),
            };
          });
        } catch (failure) {
          if (!cancelled) {
            if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
              maps.clear();
              setResult(null);
            }
            setError(failure instanceof Error ? failure.message : '位置加载失败，请重试');
          }
        } finally {
          clearTimeout(timeout);
          busy = false;
          if (!cancelled) setSettledScope(scope);
        }
      }
      void poll();
      const pollTimer = setInterval(() => void poll(), 1000);
      const clockTimer = setInterval(() => setNow(Date.now()), 250);
      return () => {
        cancelled = true;
        controller?.abort();
        clearInterval(pollTimer);
        clearInterval(clockTimer);
      };
    }, [scope, deviceId]),
  );
  const scoped = result?.scope === scope && scope ? result : null;
  return {
    map: scoped?.map ?? null,
    position: scoped?.position ?? null,
    trail: scoped?.trail ?? [],
    fresh: !!scoped?.position?.fresh && now < scoped.deadline,
    loading: !!scope && settledScope !== scope,
    error: scope && settledScope === scope ? error : '',
    reload: () => setRevision((n) => n + 1),
  };
}
