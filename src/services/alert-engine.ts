/**
 * 规则告警引擎：纯逻辑状态机，不依赖 React 和定时器（时间由 onFrame/onTick 入参驱动，方便测试）。
 * 每条规则独立运转 normal → pending(越界计数) → active → resolving(回稳计数) → normal，
 * 去抖滤单帧毛刺、滞回带防边界抖动。数据失联（dataStale）由 onTick 按时间驱动。
 * 未来接入个体基线异常检测时，实现同口径的检测器替换 ALERT_RULES 即可，对外接口不变。
 */

import { ALERT_RULES, STALE_ALERT } from '@/constants/alerts';
import type { AlertEvent, AlertRuleId, AlertSeverity } from '@/types/alerts';
import type { TelemetryFrame } from '@/types/telemetry';

type RuleRuntime = {
  violationStreak: number;
  recoveryStreak: number;
  event: AlertEvent | null;
};

export type AlertEngineOptions = {
  /** 云端连接正常但超过该时长没有新帧时触发 dataStale；replay 模式传 null 跳过 */
  staleAfterMs: number | null;
  /** 活跃告警集合发生任何变化（新增/解除/升级/折叠）时回调 */
  onChange: (alerts: AlertEvent[]) => void;
};

const SEVERITY_ORDER: Record<AlertSeverity, number> = { critical: 0, warning: 1, info: 2 };

function freshRuntime(): RuleRuntime {
  return { violationStreak: 0, recoveryStreak: 0, event: null };
}

export class AlertEngine {
  private runtimes = new Map<AlertRuleId, RuleRuntime>(
    ALERT_RULES.map((rule) => [rule.id, freshRuntime()]),
  );
  private staleRuntime = freshRuntime();
  private lastFrameAt = 0;

  constructor(private options: AlertEngineOptions) {}

  /** 每帧驱动阈值/离身/电量规则 */
  onFrame(frame: TelemetryFrame, now = Date.now()) {
    this.lastFrameAt = now;
    const ctx = { frame, health: frame.health ?? null };
    let changed = false;

    for (const rule of ALERT_RULES) {
      const state = this.runtimes.get(rule.id);
      if (!state) continue;

      if (rule.violated(ctx)) {
        state.violationStreak += 1;
        state.recoveryStreak = 0;
      } else {
        state.violationStreak = 0;
        state.recoveryStreak = rule.recovered(ctx) ? state.recoveryStreak + 1 : 0;
      }

      if (!state.event) {
        if (state.violationStreak >= rule.triggerFrames) {
          state.event = {
            ruleId: rule.id,
            label: rule.label,
            severity: rule.severity(ctx),
            triggeredAt: now,
            detail: rule.detail(ctx) ?? '',
            acknowledged: false,
          };
          changed = true;
        }
      } else if (state.recoveryStreak >= rule.resolveFrames) {
        state.event = null;
        changed = true;
      } else {
        // 活跃期间刷新数值快照与级别（电量跨过 critical 阈值时升级）
        const detail = rule.detail(ctx);
        const severity = rule.severity(ctx);
        if (
          severity !== state.event.severity ||
          (detail !== null && detail !== state.event.detail)
        ) {
          state.event = {
            ...state.event,
            severity,
            detail: detail ?? state.event.detail,
          };
          changed = true;
        }
      }
    }

    if (changed) this.emit();
  }

  /** 每秒由外部驱动：数据失联判定（仅云端已连接时，断连场景由连接状态提示接管） */
  onTick(now: number, cloudConnected: boolean) {
    const { staleAfterMs } = this.options;
    const stale =
      staleAfterMs !== null &&
      cloudConnected &&
      this.lastFrameAt > 0 &&
      now - this.lastFrameAt > staleAfterMs;

    if (stale && !this.staleRuntime.event) {
      this.staleRuntime.event = {
        ...STALE_ALERT,
        triggeredAt: now,
        detail: '',
        acknowledged: false,
      };
      this.emit();
    } else if (!stale && this.staleRuntime.event) {
      this.staleRuntime.event = null;
      this.emit();
    }
  }

  /** 折叠横幅：仅收起 UI，规则仍活跃，回稳后整体移除 */
  acknowledge(ruleId: AlertRuleId) {
    const state = ruleId === STALE_ALERT.ruleId ? this.staleRuntime : this.runtimes.get(ruleId);
    if (state?.event && !state.event.acknowledged) {
      state.event = { ...state.event, acknowledged: true };
      this.emit();
    }
  }

  getActive(): AlertEvent[] {
    const events = [...this.runtimes.values(), this.staleRuntime]
      .map((state) => state.event)
      .filter((event): event is AlertEvent => event !== null);
    events.sort(
      (a, b) =>
        SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || a.triggeredAt - b.triggeredAt,
    );
    return events;
  }

  private emit() {
    this.options.onChange(this.getActive());
  }
}
