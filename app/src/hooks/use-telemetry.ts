/**
 * 订阅云端遥测数据流：连接状态 + 展示状态 + 最新有效帧 + 指标时间序列（环形缓冲）。
 * health.valid === false 的帧（传感器未贴好）只更新 battery，不进入序列。
 */

import { useEffect, useRef, useState } from 'react';

import { telemetryService } from '@/services/telemetry-service';
import type {
  SeriesPoint,
  TelemetryDisplayState,
  TelemetryFrame,
  TelemetrySeries,
  TelemetryStatus,
} from '@/types/telemetry';

const MAX_POINTS = 90;
/** 帧新鲜窗口：超过此时长没有新帧视为设备侧无数据（只用本机时钟，避免与服务器时钟偏差误判） */
const FRESH_WINDOW_MS = 10_000;

const EMPTY_SERIES: TelemetrySeries = { heartRate: [], spo2: [], bodyTemp: [] };

function pushPoint(
  points: SeriesPoint[],
  value: number,
  t: number,
  max = MAX_POINTS,
): SeriesPoint[] {
  if (!Number.isFinite(value) || value <= 0) return points;
  const next = [...points, { t, v: value }];
  return next.length > max ? next.slice(next.length - max) : next;
}

export function useTelemetry() {
  const [status, setStatus] = useState<TelemetryStatus>(() => telemetryService.getStatus());
  const [latest, setLatest] = useState<TelemetryFrame | null>(null);
  const [series, setSeries] = useState<TelemetrySeries>(EMPTY_SERIES);
  const seriesRef = useRef(EMPTY_SERIES);
  const lastSample = useRef<{ deviceId: string; tsMs: number; chartTime: number } | null>(null);
  const [lastArrival, setLastArrival] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const unsubscribeStatus = telemetryService.subscribeStatus(setStatus);
    const unsubscribeFrame = telemetryService.subscribeFrame((frame) => {
      const previous = lastSample.current;
      if (previous?.deviceId === frame.deviceId && previous.tsMs === frame.tsMs) return;
      const reset = !previous || previous.deviceId !== frame.deviceId || frame.tsMs < previous.tsMs;
      const chartTime = reset
        ? Date.parse(frame.receivedAt)
        : previous.chartTime + frame.tsMs - previous.tsMs;
      lastSample.current = { deviceId: frame.deviceId, tsMs: frame.tsMs, chartTime };
      if (reset) {
        seriesRef.current = EMPTY_SERIES;
        setSeries(EMPTY_SERIES);
      }
      setLatest(frame);
      setLastArrival(Date.now());
      const health = frame.health;
      if (!health?.valid) return;
      const next: TelemetrySeries = {
        heartRate: pushPoint(seriesRef.current.heartRate, health.heartRate, chartTime),
        spo2: pushPoint(seriesRef.current.spo2, health.spo2, chartTime),
        bodyTemp: pushPoint(seriesRef.current.bodyTemp, health.bodyTemp, chartTime),
      };
      seriesRef.current = next;
      setSeries(next);
    });
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(timer);
      unsubscribeStatus();
      unsubscribeFrame();
    };
  }, []);

  // 新鲜度只比较本机时钟的帧到达时间；receivedAt 是服务器时钟，二者有偏差时不可比
  const isFresh =
    status === 'open' && lastArrival > 0 && now - lastArrival < FRESH_WINDOW_MS && !!latest;

  const displayState: TelemetryDisplayState =
    status === 'replaying'
      ? 'replaying'
      : status === 'open'
        ? isFresh
          ? 'streaming'
          : 'waiting_device'
        : status === 'closed'
          ? 'offline'
          : status;

  return { status, displayState, latest, series, isFresh };
}
