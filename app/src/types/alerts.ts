/** 规则告警相关类型。规则阈值定义在 src/constants/alerts.ts，状态机在 src/services/alert-engine.ts */

export type AlertRuleId =
  | 'heartRateHigh'
  | 'heartRateLow'
  | 'bodyTempHigh'
  | 'bodyTempLow'
  | 'spo2Low'
  | 'sensorOff'
  | 'batteryLow'
  | 'dataStale';

export type AlertSeverity = 'critical' | 'warning' | 'info';

/** 一条处于活跃状态的告警 */
export type AlertEvent = {
  ruleId: AlertRuleId;
  label: string;
  severity: AlertSeverity;
  /** 触发时刻（Date.now() 毫秒） */
  triggeredAt: number;
  /** 触发时的数值快照，如 "192 bpm"；失联类告警为空字符串 */
  detail: string;
  /** 用户已折叠：仅收起横幅，规则仍活跃，指标回稳后整体移除 */
  acknowledged: boolean;
};
