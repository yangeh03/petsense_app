/**
 * 告警规则配置（v1：固定阈值版）。
 * 阈值取犬类兽医参考范围：静息心率 60–140（小体型偏高）、体温 38.3–39.2℃、血氧应 ≥95%。
 * 后续接入个体基线异常检测（冷启动学习期建基线）时，替换本表为基线检测器即可，状态机不变。
 */

import type { AlertRuleId, AlertSeverity } from '@/types/alerts';
import type { TelemetryFrame, TelemetryHealth } from '@/types/telemetry';

/** 规则求值上下文：health 为 null 表示该帧没有健康数据，规则应保持现状 */
export type AlertRuleContext = {
  frame: TelemetryFrame;
  health: TelemetryHealth | null;
};

export type AlertRule = {
  id: AlertRuleId;
  label: string;
  /** 级别可在活跃期间变化（如电量 <10% 从 warning 升级为 critical） */
  severity: (ctx: AlertRuleContext) => AlertSeverity;
  /** 连续越界多少帧后触发（遥测约 2s/帧） */
  triggerFrames: number;
  /** 连续回稳多少帧后解除 */
  resolveFrames: number;
  violated: (ctx: AlertRuleContext) => boolean;
  recovered: (ctx: AlertRuleContext) => boolean;
  /** 活跃期间展示的数值快照；返回 null 表示维持上一个快照 */
  detail: (ctx: AlertRuleContext) => string | null;
};

type MetricKey = 'heartRate' | 'spo2' | 'bodyTemp';

/** 数值阈值规则：above = 超过 trigger 触发、跌回 resolve 以下解除（两者构成滞回带） */
function thresholdRule(options: {
  id: AlertRuleId;
  label: string;
  severity: AlertSeverity;
  metric: MetricKey;
  direction: 'above' | 'below';
  trigger: number;
  resolve: number;
  triggerFrames?: number;
  resolveFrames?: number;
  decimals: number;
  unit: string;
}): AlertRule {
  const { metric, direction, trigger, resolve, decimals, unit } = options;
  const format = (value: number) => `${value.toFixed(decimals)} ${unit}`;
  const reading = (ctx: AlertRuleContext) =>
    ctx.health && ctx.health.valid && ctx.health[metric] > 0 ? ctx.health[metric] : null;
  const inBand = (value: number, edge: number) =>
    direction === 'above' ? value < edge : value > edge;
  return {
    id: options.id,
    label: options.label,
    severity: () => options.severity,
    triggerFrames: options.triggerFrames ?? 5,
    resolveFrames: options.resolveFrames ?? 3,
    violated: (ctx) => {
      const value = reading(ctx);
      return value !== null && (direction === 'above' ? value > trigger : value < trigger);
    },
    recovered: (ctx) => {
      const value = reading(ctx);
      return value !== null && inBand(value, resolve);
    },
    detail: (ctx) => {
      const value = reading(ctx);
      return value === null ? null : format(value);
    },
  };
}

/** 心率/体温/血氧阈值规则（触发/解除阈值留缓冲带，防边界抖动） */
export const ALERT_RULES: AlertRule[] = [
  thresholdRule({
    id: 'heartRateHigh',
    label: 'Fast heart rate',
    severity: 'critical',
    metric: 'heartRate',
    direction: 'above',
    trigger: 180,
    resolve: 165,
    decimals: 0,
    unit: 'bpm',
  }),
  thresholdRule({
    id: 'heartRateLow',
    label: 'Slow heart rate',
    severity: 'critical',
    metric: 'heartRate',
    direction: 'below',
    trigger: 60,
    resolve: 75,
    decimals: 0,
    unit: 'bpm',
  }),
  thresholdRule({
    id: 'bodyTempHigh',
    label: 'Fever',
    severity: 'critical',
    metric: 'bodyTemp',
    direction: 'above',
    trigger: 39.5,
    resolve: 39.0,
    decimals: 1,
    unit: '℃',
  }),
  thresholdRule({
    id: 'bodyTempLow',
    label: 'Low body temperature',
    severity: 'warning',
    metric: 'bodyTemp',
    direction: 'below',
    trigger: 37.5,
    resolve: 38.0,
    decimals: 1,
    unit: '℃',
  }),
  thresholdRule({
    id: 'spo2Low',
    label: 'Low SpO₂',
    severity: 'critical',
    metric: 'spo2',
    direction: 'below',
    trigger: 90,
    resolve: 92,
    decimals: 0,
    unit: '%',
  }),
  {
    id: 'sensorOff',
    label: 'Sensor off body',
    severity: () => 'warning',
    // 2s/帧 × 90 帧 ≈ 3 分钟持续无效才告警，短暂松动不打扰
    triggerFrames: 90,
    resolveFrames: 1,
    violated: (ctx) => ctx.health !== null && !ctx.health.valid,
    recovered: (ctx) => ctx.health?.valid === true,
    detail: () => null,
  },
  {
    id: 'batteryLow',
    label: 'Collar battery low',
    severity: (ctx) =>
      ctx.frame.battery?.found && ctx.frame.battery.percent < BATTERY_CRITICAL_BELOW
        ? 'critical'
        : 'warning',
    triggerFrames: 3,
    resolveFrames: 3,
    violated: (ctx) => {
      const battery = ctx.frame.battery;
      return !!battery?.found && battery.percent < BATTERY_WARNING_BELOW;
    },
    recovered: (ctx) => {
      const battery = ctx.frame.battery;
      return !!battery?.found && battery.percent > BATTERY_RESOLVE_ABOVE;
    },
    detail: (ctx) => {
      const battery = ctx.frame.battery;
      return battery?.found ? `${Math.round(battery.percent)} %` : null;
    },
  },
];

export const BATTERY_WARNING_BELOW = 20;
export const BATTERY_CRITICAL_BELOW = 10;
export const BATTERY_RESOLVE_ABOVE = 25;

/** 数据失联规则（时间驱动，由引擎直接实现，不进 ALERT_RULES） */
export const STALE_ALERT = {
  ruleId: 'dataStale',
  label: 'Data feed interrupted',
  severity: 'info',
} as const;

/** 云端连接正常但超过该时长没有新帧时判定设备数据中断 */
export const STALE_AFTER_MS = 30_000;
