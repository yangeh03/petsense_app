/**
 * 订阅遥测帧驱动告警引擎，暴露当前活跃告警与折叠操作。
 * 引擎为模块级共享单例，通过 useSyncExternalStore 对外发布：
 * 铃铛 / 头像状态环 / 洞察气泡等所有消费方看到同一份状态（包括 acknowledge）。
 * 首个订阅者挂载时才开始计算，全部卸载后停止。
 * live 模式启用数据失联检测（云端断开时由连接状态行提示，避免双重告警），replay 模式跳过。
 */

import { useCallback, useSyncExternalStore } from 'react';

import { TELEMETRY_MODE } from '@/config';
import { STALE_AFTER_MS } from '@/constants/alerts';
import { AlertEngine } from '@/services/alert-engine';
import { telemetryService } from '@/services/telemetry-service';
import type { AlertEvent, AlertRuleId, AlertSeverity } from '@/types/alerts';

type Listener = () => void;

const listeners = new Set<Listener>();
let snapshot: AlertEvent[] = [];
let refCount = 0;
let teardown: (() => void) | null = null;

const engine = new AlertEngine({
  staleAfterMs: TELEMETRY_MODE === 'live' ? STALE_AFTER_MS : null,
  onChange: (alerts) => {
    snapshot = alerts;
    listeners.forEach((listener) => listener());
  },
});

function subscribe(listener: Listener) {
  listeners.add(listener);
  refCount += 1;
  if (!teardown) {
    const unsubscribeStatus = telemetryService.subscribeStatus((status) => {
      engine.onTick(Date.now(), status === 'open');
    });
    const unsubscribeFrame = telemetryService.subscribeFrame((frame) => engine.onFrame(frame));
    const timer = setInterval(
      () => engine.onTick(Date.now(), telemetryService.getStatus() === 'open'),
      1000,
    );
    teardown = () => {
      clearInterval(timer);
      unsubscribeStatus();
      unsubscribeFrame();
    };
  }
  return () => {
    listeners.delete(listener);
    refCount -= 1;
    if (refCount <= 0 && teardown) {
      teardown();
      teardown = null;
      refCount = 0;
    }
  };
}

const getSnapshot = () => snapshot;

export function useAlerts() {
  const activeAlerts = useSyncExternalStore(subscribe, getSnapshot);
  const acknowledge = useCallback((ruleId: AlertRuleId) => engine.acknowledge(ruleId), []);
  return { activeAlerts, acknowledge };
}

/** 全部告警中最严重的级别；无告警返回 null */
export function worstSeverity(alerts: AlertEvent[]): AlertSeverity | null {
  return alerts.reduce<AlertSeverity | null>(
    (worst, alert) =>
      worst === 'critical' || alert.severity === 'critical'
        ? 'critical'
        : worst === 'warning' || alert.severity === 'warning'
          ? 'warning'
          : 'info',
    null,
  );
}
