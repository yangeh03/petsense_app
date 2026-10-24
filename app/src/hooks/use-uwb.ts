/**
 * 订阅 UWB 定位流：最新位置 + 渐隐轨迹（环形缓冲）+ 数据新鲜度。
 * 当前固件尚未接入 UWB 时 hasData 恒为 false，页面据此展示诚实空态。
 */

import { useEffect, useRef, useState } from 'react';

import { positionFromUwb } from '@/services/uwb-position';
import { telemetryService } from '@/services/telemetry-service';
import type { TelemetryUwb } from '@/types/telemetry';

const MAX_TRAIL = 40;
const FRESH_MS = 15_000;

export function useUwb() {
  const [uwb, setUwb] = useState<TelemetryUwb | null>(null);
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null);
  const [trail, setTrail] = useState<{ x: number; y: number }[]>([]);
  const [lastUpdate, setLastUpdate] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const trailRef = useRef<{ x: number; y: number }[]>([]);

  useEffect(() => {
    const unsubscribe = telemetryService.subscribeFrame((frame) => {
      if (!frame.uwb) return;
      setUwb(frame.uwb);
      setLastUpdate(Date.now());
      const solved = positionFromUwb(frame.uwb);
      if (solved) {
        setPosition(solved);
        trailRef.current = [...trailRef.current.slice(-(MAX_TRAIL - 1)), solved];
        setTrail(trailRef.current);
      }
    });
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(timer);
      unsubscribe();
    };
  }, []);

  const hasData = uwb !== null && lastUpdate > 0 && now - lastUpdate < FRESH_MS;
  return { uwb, position, trail, hasData, lastUpdate };
}
