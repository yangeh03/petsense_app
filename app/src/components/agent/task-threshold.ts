/**
 * W08 确认卡的阈值换算（agent-w08-spec.md §2）：编辑阈值不得改变未编辑的数值。
 *
 * 阈值在服务端以「秒（走动）」或「次（声音事件）」存储，输入框按自然单位显示：
 * 走动用分钟，声音事件用次数。来回换算必须可逆且保精度——声音事件次数不得被
 * 当作秒再除以 60（3 次会变成 0），分钟也不得被四舍五入丢精度。
 */

export type TaskThresholdCondition = {
  metric: 'walkSeconds' | 'soundEventCount';
  op: 'at_least' | 'at_most';
  value: number;
  scope: 'window' | 'daily';
};

/** 输入框的单位标签：走动=分钟，声音事件=次 */
export function thresholdUnitLabel(metric: TaskThresholdCondition['metric']): string {
  return metric === 'walkSeconds' ? '分钟' : '次';
}

/** 阈值输入框的显示值（保留原始精度；声音次数原样返回） */
export function thresholdDisplayValue(condition: TaskThresholdCondition): string {
  if (condition.metric === 'walkSeconds') {
    const minutes = condition.value / 60;
    return Number.isInteger(minutes) ? String(minutes) : String(Number(minutes.toFixed(2)));
  }
  return String(condition.value);
}

/**
 * 输入框的值转回服务端阈值；输入非法（非空、有限、非负）时返回 null。
 * 调用方须在 null 时提示用户并中止提交，不得静默改成 0 或原值。
 */
export function thresholdToValue(condition: TaskThresholdCondition, raw: string): number | null {
  const text = (raw ?? '').trim();
  if (!text) return null;
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  if (condition.metric === 'soundEventCount') {
    if (!Number.isInteger(parsed)) return null;
    return parsed;
  }
  return Math.round(parsed * 60);
}

/** 计划里的提醒条件文案（分钟/次，带单位） */
export function conditionSummary(condition: TaskThresholdCondition): string {
  const unit = thresholdUnitLabel(condition.metric);
  const value =
    condition.metric === 'walkSeconds'
      ? String(Number((condition.value / 60).toFixed(2)))
      : String(condition.value);
  const direction = condition.op === 'at_least' ? '达到或超过' : '不多于';
  const scope = condition.scope === 'daily' ? '单日' : '累计';
  return `${scope} ${direction} ${value} ${unit}`;
}
