/**
 * 订阅云端遥测数据流：连接状态 + 最新有效帧 + 指标时间序列（环形缓冲）。
 * health.valid === false 的帧（传感器未贴好）只更新 battery，不进入序列。
 */

import { useEffect, useRef, useState } from 'react';

import { telemetryService } from '@/services/telemetry-service';
import type {
  SeriesPoint,
  TelemetryFrame,
  TelemetrySeries,
  TelemetryStatus,
} from '@/types/telemetry';

const MAX_POINTS = 90;

const EMPTY_SERIES: TelemetrySeries = { heartRate: [], spo2: [], bodyTemp: [] };

function pushPoint(points: SeriesPoint[], value: number, max = MAX_POINTS): SeriesPoint[] {
  const next = [...points, { t: Date.now(), v: value }];
  return next.length > max ? next.slice(next.length - max) : next;
}

export function useTelemetry() {
  const [status, setStatus] = useState<TelemetryStatus>(() => telemetryService.getStatus());
  const [latest, setLatest] = useState<TelemetryFrame | null>(null);
  const [series, setSeries] = useState<TelemetrySeries>(EMPTY_SERIES);
  const seriesRef = useRef(EMPTY_SERIES);

  useEffect(() => {
    const unsubscribeStatus = telemetryService.subscribeStatus(setStatus);
    const unsubscribeFrame = telemetryService.subscribeFrame((frame) => {
      setLatest(frame);
      const health = frame.health;
      if (!health?.valid) return;
      const next: TelemetrySeries = {
        heartRate: pushPoint(seriesRef.current.heartRate, health.heartRate),
        spo2: pushPoint(seriesRef.current.spo2, health.spo2),
        bodyTemp: pushPoint(seriesRef.current.bodyTemp, health.bodyTemp),
      };
      seriesRef.current = next;
      setSeries(next);
    });
    return () => {
      unsubscribeStatus();
      unsubscribeFrame();
    };
  }, []);

  return { status, latest, series };
}
