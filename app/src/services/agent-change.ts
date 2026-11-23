/**
 * W11 个人变化合流（agent-w11-spec.md）：确认选择与冻结证据的展示口径。
 *
 * 纯函数模块（不依赖 React）：确认卡的两项基线选择、冻结证据的展示行与来源下钻
 * 文案，便于单测覆盖，也便于卡片与详情页共用同一口径。
 */

export type AgentBaselineStrategy = 'fact_first' | 'wait_baseline';

export type AgentChangeRange = { p10?: number; p50?: number; p90?: number } | null;

/** 服务端冻结的个人比较证据（D6）：样本窗口、版本与修订号、分位数、实际值与方向。 */
export type AgentChangeEvidence = {
  evidenceId: string;
  feature: string;
  dimension?: string | null;
  metric?: string | null;
  unit?: string | null;
  date: string;
  slot: number;
  slotLabel?: string | null;
  slotStartAt?: string | null;
  slotEndAt?: string | null;
  value?: number | null;
  coverageSeconds?: number | null;
  coverageMinutes?: number | null;
  samples?: number | null;
  sampleStartDay?: string | null;
  sampleEndDay?: string | null;
  version?: string | null;
  revision?: number | null;
  range?: AgentChangeRange;
  direction?: 'above' | 'below' | null;
};

export const BASELINE_STRATEGY_OPTIONS: {
  value: AgentBaselineStrategy;
  label: string;
  hint: string;
}[] = [
  { value: 'wait_baseline', label: '等基线就绪', hint: '基线够了再开始跟平时比较' },
  { value: 'fact_first', label: '先记录绝对事实', hint: '先记事实，不做个人比较' },
];

/** 基线未就绪时，确认卡必须由主人明确选择（缺少选择不静默生效）。 */
export function requiresStrategyChoice(
  plan:
    | {
        baselineStrategyChoiceRequired?: boolean;
      }
    | null
    | undefined,
): boolean {
  return !!plan?.baselineStrategyChoiceRequired;
}

export function strategyLabel(strategy: AgentBaselineStrategy | null | undefined): string {
  if (strategy === 'wait_baseline') return '等基线就绪';
  if (strategy === 'fact_first') return '先记录绝对事实';
  return '未选择';
}

export function directionLabel(direction: 'above' | 'below' | null | undefined): string {
  if (direction === 'above') return '高于平时范围';
  if (direction === 'below') return '低于平时范围';
  return '在平时范围内';
}

/** 指标数值按单位展示：走动=分钟，声音=次数。 */
export function formatMetricValue(evidence: AgentChangeEvidence): string {
  if (evidence.value == null) return '无数据';
  if (evidence.unit === 'count') return `${evidence.value} 次`;
  return `${Math.round((evidence.value / 60) * 10) / 10} 分钟`;
}

export function formatRangeValue(range: AgentChangeRange | undefined, unit?: string | null) {
  if (!range || range.p10 == null || range.p90 == null) return '—';
  const fmt = (value: number) =>
    unit === 'count' ? `${value} 次` : `${Math.round((value / 60) * 10) / 10} 分钟`;
  return `${fmt(range.p10)} – ${fmt(range.p90)}`;
}

/** 冻结证据的展示行（顺序固定，便于视觉走查比对）。 */
export function evidenceRows(evidence: AgentChangeEvidence): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  rows.push({ label: '时段', value: evidence.slotLabel || `时段 ${evidence.slot}` });
  rows.push({ label: '实际', value: formatMetricValue(evidence) });
  rows.push({ label: '平时范围', value: formatRangeValue(evidence.range, evidence.unit) });
  rows.push({
    label: '对比',
    value: `${directionLabel(evidence.direction)}（覆盖 ${evidence.coverageMinutes ?? 0} 分钟）`,
  });
  if (evidence.sampleStartDay || evidence.sampleEndDay) {
    rows.push({
      label: '样本',
      value: `${evidence.sampleStartDay ?? '?'} – ${evidence.sampleEndDay ?? '?'}（${
        evidence.samples ?? 0
      } 天）`,
    });
  }
  if (evidence.version) {
    rows.push({
      label: '基线版本',
      value: `${evidence.version}${evidence.revision != null ? `（第 ${evidence.revision} 次重算）` : ''}`,
    });
  }
  return rows;
}

/** 来源下钻的时段范围（App 用它定位到对应记录）。 */
export function evidenceWindow(evidence: AgentChangeEvidence): {
  startAt: string | null;
  endAt: string | null;
} {
  return { startAt: evidence.slotStartAt ?? null, endAt: evidence.slotEndAt ?? null };
}
