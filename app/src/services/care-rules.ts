/**
 * W09 受管理告警规则（agent-w09-spec.md §3 D3）：后端已接管的硬件规则能力探测与本地抑制。
 *
 * 服务端把低电、失联、长期无效三类迁到云端主动提醒；新 App 在「已获知后端接管」时
 * 仅抑制同一宠物的这三类本地重复提醒（设备状态展示不受影响，其他告警照常）。
 * 切宠、退出、请求失败或能力未确认时恢复原逻辑。模块级共享状态 + 订阅通知，
 * useAlerts 据此过滤活跃告警集合。
 */

import { request } from '@/services/api';
import type { AlertEvent, AlertRuleId } from '@/types/alerts';

export type ManagedCareRule = 'battery_low' | 'data_stale' | 'readings_invalid';

export type CareRulesCapability = {
  capability: string;
  managedRules: ManagedCareRule[];
};

/** 后端规则 ID → 本地告警规则 ID（阈值语义一致，见 constants/alerts.ts 与服务端 care.py） */
export const CARE_RULE_TO_ALERT_RULE: Record<ManagedCareRule, AlertRuleId> = {
  battery_low: 'batteryLow',
  data_stale: 'dataStale',
  readings_invalid: 'sensorOff',
};

const FETCH_TTL_MS = 5 * 60_000;

let managedRules: ManagedCareRule[] | null = null; // null = 能力未确认（本地告警照常）
let fetchedForPet: number | null = null;
let fetching: Promise<void> | null = null;
let fetchedAt = 0;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((listener) => listener());
}

/** 订阅受管理规则变化（抑制集合变化时重算本地告警快照） */
export function subscribeCareRules(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 当前已确认的受管理规则；null 表示未确认（不抑制任何本地告警） */
export function managedCareRules(): ManagedCareRule[] | null {
  return managedRules;
}

/** 纯过滤：抑制与受管理规则同类的本地告警（设备状态展示不走本列表） */
export function suppressManagedAlerts(
  alerts: AlertEvent[],
  rules: ManagedCareRule[] | null,
): AlertEvent[] {
  if (!rules || rules.length === 0) return alerts;
  const suppressed = new Set<AlertRuleId>(rules.map((rule) => CARE_RULE_TO_ALERT_RULE[rule]));
  return alerts.filter((alert) => !suppressed.has(alert.ruleId));
}

/** 清除已确认能力（退出登录 / 切宠 / 探测失败）：恢复本地告警逻辑 */
export function clearCareRules() {
  const changed = managedRules !== null || fetchedForPet !== null;
  managedRules = null;
  fetchedForPet = null;
  fetching = null;
  fetchedAt = 0;
  if (changed) notify();
}

/**
 * 探测宠物的受管理规则能力（带 TTL 去抖；同宠 5 分钟内不重复请求）。
 * 失败按「无法确认能力」处理：清空并恢复本地规则（不重试轰炸）。
 */
export async function refreshCareRules(petId: number | null): Promise<void> {
  if (petId == null) {
    clearCareRules();
    return;
  }
  if (fetchedForPet === petId && Date.now() - fetchedAt < FETCH_TTL_MS) return;
  if (fetching && fetchedForPet === petId) return fetching;
  fetchedForPet = petId;
  fetching = (async () => {
    try {
      const data = await request<CareRulesCapability>(`/agent/pets/${petId}/care/rules`);
      managedRules = Array.isArray(data.managedRules)
        ? data.managedRules.filter((rule) => rule in CARE_RULE_TO_ALERT_RULE)
        : [];
      fetchedAt = Date.now();
    } catch {
      managedRules = null; // 能力缺失/后端未接管：恢复原逻辑
      fetchedAt = 0;
    } finally {
      fetching = null;
      notify();
    }
  })();
  return fetching;
}
