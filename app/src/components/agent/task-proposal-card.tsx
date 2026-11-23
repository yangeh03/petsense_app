/**
 * W08 委托确认卡（agent-w08-spec.md §2）：展示计划全文与基线选择，可编辑并重新提交
 * 归一化计划（新候选替代旧卡），确认/拒绝走与档案卡相同的决策端点（幂等，409 翻终态）。
 * 编辑器覆盖可安全结构化的字段：截止（快捷档）、阈值（数值/方向/口径）与基线策略；
 * 更大的改动引导用自然语言重新发起。
 */

import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { SymbolView } from 'expo-symbols';

import { ThemedText } from '@/components/themed-text';
import {
  conditionSummary,
  thresholdDisplayValue,
  thresholdToValue,
  thresholdUnitLabel,
} from '@/components/agent/task-threshold';
import { useTheme } from '@/hooks/use-theme';
import {
  BASELINE_STRATEGY_OPTIONS,
  requiresStrategyChoice,
  type AgentBaselineStrategy,
} from '@/services/agent-change';
import { ApiError } from '@/services/api';
import {
  decideAgentProposal,
  submitAgentProposalEdit,
  type AgentProposal,
  type AgentTaskChangeFields,
  type AgentTaskPlan,
  type AgentTurn,
} from '@/services/agent';
import { useAgentChatStore } from '@/stores/agent-chat';

type DecisionConflict = { proposal?: AgentProposal; turn?: AgentTurn | null };

const SH_TZ = 'Asia/Shanghai';
const KIND_LABELS: Record<AgentTaskPlan['kind'], string> = {
  fact_watch: '事实观察',
  comparison: '个人比较',
  scheduled_review: '定时回顾',
};
const ACTION_LABELS: Record<AgentTaskChangeFields['action'], string> = {
  create: '新建委托',
  update: '修改委托',
  renew: '续期委托',
  pause: '暂停委托',
  resume: '恢复委托',
  cancel: '取消委托',
};
const FEATURE_LABELS = { actions: '走动', sounds: '声音事件' } as const;

function formatLocal(iso: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SH_TZ,
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

function shanghaiEndOfDay(offsetDays: number): string {
  const now = new Date();
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: SH_TZ,
    dateStyle: 'short',
  }).format(now);
  const [year, month, day] = parts.split('-').map(Number);
  const end = Date.UTC(year, month - 1, day) + offsetDays * 86400000;
  return new Date(end).toISOString().replace('.000Z', '+08:00');
}

function deadlinePresets(): { label: string; value: string }[] {
  const now = new Date();
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: SH_TZ, dateStyle: 'short' }).format(
    now,
  );
  const [y, m, d] = today.split('-').map(Number);
  const weekDayIndex = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0=周日
  const daysToSunday = (7 - weekDayIndex) % 7 || 7;
  return [
    { label: '明天结束', value: shanghaiEndOfDay(1) },
    { label: '3 天后', value: shanghaiEndOfDay(3) },
    { label: '本周日结束', value: shanghaiEndOfDay(daysToSunday) },
    { label: '7 天后', value: shanghaiEndOfDay(7) },
  ];
}

function conditionText(plan: AgentTaskPlan): string | null {
  if (!plan.condition) return null;
  const feature = plan.feature ? FEATURE_LABELS[plan.feature] : '';
  return `${feature} ${conditionSummary(plan.condition)}`;
}

function planRows(plan: AgentTaskPlan): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [
    { label: '类型', value: KIND_LABELS[plan.kind] },
    { label: '关注', value: plan.question },
  ];
  const condition = conditionText(plan);
  if (condition) rows.push({ label: '提醒条件', value: condition });
  if (plan.comparison) {
    rows.push({
      label: '比较方向',
      value: plan.comparison.band === 'below' ? '低于它的平时范围' : '高于它的平时范围',
    });
  }
  rows.push({
    label: '观察窗口',
    value: `${formatLocal(plan.startAt)} – ${formatLocal(plan.deadlineAt)}（Asia/Shanghai）`,
  });
  if (requiresStrategyChoice(plan)) {
    rows.push({ label: '基线选择', value: '需要你选：等基线就绪，或先记录绝对事实' });
  } else if (plan.baselineStrategy) {
    rows.push({
      label: '基线选择',
      value: plan.baselineStrategy === 'wait_baseline' ? '等基线就绪后开始比较' : '先按事实观察',
    });
  }
  return rows;
}

export function TaskProposalCard({
  petId,
  proposal,
}: {
  petId: number | null;
  proposal: AgentProposal;
}) {
  const theme = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const applyTurn = useAgentChatStore((state) => state.applyTurn);
  const fields = proposal.fields as unknown as AgentTaskChangeFields;
  const plan = fields.plan ?? null;
  const presets = useMemo(() => deadlinePresets(), []);
  // 阈值按自然单位显示（走动=分钟，声音=次数）且保精度；未编辑就原样保留（task-threshold.ts）
  const [threshold, setThreshold] = useState(
    plan?.condition ? thresholdDisplayValue(plan.condition) : '',
  );
  const [thresholdDirty, setThresholdDirty] = useState(false);
  const [op, setOp] = useState<'at_least' | 'at_most'>(plan?.condition?.op ?? 'at_least');
  const [scope, setScope] = useState<'window' | 'daily'>(plan?.condition?.scope ?? 'window');
  const [strategy, setStrategy] = useState<'fact_first' | 'wait_baseline'>(
    plan?.baselineStrategy ?? 'fact_first',
  );
  const [deadline, setDeadline] = useState<string | null>(null);
  // W11 D3：基线未就绪时必须由主人明确选择（服务端不接受缺少选择的确认）
  const choiceRequired = requiresStrategyChoice(plan);
  const [strategyChoice, setStrategyChoice] = useState<AgentBaselineStrategy | null>(
    choiceRequired ? null : (plan?.baselineStrategy ?? null),
  );

  const decide = async (action: 'confirm' | 'reject') => {
    if (busy || proposal.status !== 'pending' || petId == null) return;
    setBusy(true);
    setError('');
    if (action === 'confirm' && choiceRequired && !strategyChoice) {
      setError('请先选择「等基线就绪」或「先记录绝对事实」');
      return;
    }
    try {
      const result = await decideAgentProposal(
        petId,
        proposal.id,
        action,
        action === 'confirm' ? (strategyChoice ?? undefined) : undefined,
      );
      if (result.turn) applyTurn(result.turn);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 409) {
        const conflict = exc.payload as DecisionConflict | undefined;
        if (conflict?.turn) {
          applyTurn(conflict.turn);
          return;
        }
      }
      setError(exc instanceof Error && exc.message ? exc.message : '操作失败，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  const resubmit = async () => {
    if (busy || petId == null || !plan) return;
    setBusy(true);
    setError('');
    const patch: Record<string, unknown> = {};
    if (deadline) patch.deadlineAt = deadline;
    if (plan.condition) {
      const next: Record<string, unknown> = { ...plan.condition, op, scope };
      if (thresholdDirty) {
        // 只在用户真的改过阈值时才覆盖数值；非法输入中止提交，不静默改值
        const value = thresholdToValue(plan.condition, threshold);
        if (value === null) {
          setError('阈值需填非负数字');
          setBusy(false);
          return;
        }
        next.value = value;
      }
      patch.condition = next;
    }
    if (plan.baselineStrategy) patch.baselineStrategy = strategy;
    try {
      await submitAgentProposalEdit(petId, proposal.id, patch as Partial<AgentTaskPlan>);
      setEditing(false); // 新候选由 WS/409 响应刷新旧卡；确认最新版本即可
    } catch (exc) {
      setError(exc instanceof Error && exc.message ? exc.message : '修改没能提交，请再试');
    } finally {
      setBusy(false);
    }
  };

  const statusText =
    proposal.status === 'confirmed'
      ? fields.action === 'cancel'
        ? '已取消委托'
        : '已生效'
      : proposal.status === 'rejected'
        ? '已拒绝'
        : proposal.status === 'superseded'
          ? '已有新候选'
          : null;
  const editable = proposal.status === 'pending' && plan;

  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.head}>
        <SymbolView
          name={{ ios: 'checklist', android: 'checklist', web: 'checklist' }}
          tintColor={theme.tint}
          size={16}
        />
        <ThemedText type="smallBold" style={styles.headText}>
          {ACTION_LABELS[fields.action]}
          {plan ? `：${plan.title}` : ''}
        </ThemedText>
        {statusText ? (
          <ThemedText
            type="small"
            style={{ color: proposal.status === 'confirmed' ? theme.online : theme.textSecondary }}
          >
            {statusText}
          </ThemedText>
        ) : null}
      </View>
      {plan ? (
        <>
          {planRows(plan).map((row) => (
            <View key={row.label} style={styles.row}>
              <ThemedText type="small" themeColor="textSecondary">
                {row.label}
              </ThemedText>
              <ThemedText type="small" style={styles.rowValue}>
                {row.value}
              </ThemedText>
            </View>
          ))}
        </>
      ) : (
        <ThemedText type="small" themeColor="textSecondary">
          {fields.taskId ? `委托 #${fields.taskId}（第 ${fields.expectedVersion} 版）` : ''}
        </ThemedText>
      )}
      {editable && editing ? (
        <View style={[styles.editor, { borderColor: theme.backgroundSelected }]}>
          {plan.condition ? (
            <>
              <ThemedText type="smallBold">调整提醒条件</ThemedText>
              <View style={styles.optionRow}>
                {(
                  [
                    ['at_least', '达到或超过'],
                    ['at_most', '不多于'],
                  ] as const
                ).map(([value, label]) => (
                  <Pressable
                    key={value}
                    accessibilityRole="button"
                    onPress={() => setOp(value)}
                    style={[
                      styles.option,
                      { borderColor: theme.backgroundSelected },
                      op === value && {
                        backgroundColor: theme.tintBackground,
                        borderColor: theme.tint,
                      },
                    ]}
                  >
                    <ThemedText type="small">{label}</ThemedText>
                  </Pressable>
                ))}
              </View>
              <View style={styles.optionRow}>
                {(
                  [
                    ['window', '整个期间累计'],
                    ['daily', '按单日'],
                  ] as const
                ).map(([value, label]) => (
                  <Pressable
                    key={value}
                    accessibilityRole="button"
                    onPress={() => setScope(value)}
                    style={[
                      styles.option,
                      { borderColor: theme.backgroundSelected },
                      scope === value && {
                        backgroundColor: theme.tintBackground,
                        borderColor: theme.tint,
                      },
                    ]}
                  >
                    <ThemedText type="small">{label}</ThemedText>
                  </Pressable>
                ))}
              </View>
              <TextInput
                accessibilityLabel="提醒阈值"
                value={threshold}
                onChangeText={(text) => {
                  setThreshold(text);
                  setThresholdDirty(true);
                }}
                keyboardType="number-pad"
                style={[
                  styles.thresholdInput,
                  { borderColor: theme.backgroundSelected, color: theme.text },
                ]}
              />
              <ThemedText type="small" themeColor="textSecondary">
                单位：{thresholdUnitLabel(plan.condition.metric)}
                {plan.condition.metric === 'walkSeconds' ? '（走动）' : '（声音事件）'}
                ，留空不改
              </ThemedText>
            </>
          ) : null}
          {plan.baselineStrategy ? (
            <>
              <ThemedText type="smallBold">基线选择</ThemedText>
              <View style={styles.optionRow}>
                {(
                  [
                    ['fact_first', '先按事实观察'],
                    ['wait_baseline', '等基线就绪'],
                  ] as const
                ).map(([value, label]) => (
                  <Pressable
                    key={value}
                    accessibilityRole="button"
                    onPress={() => setStrategy(value)}
                    style={[
                      styles.option,
                      { borderColor: theme.backgroundSelected },
                      strategy === value && {
                        backgroundColor: theme.tintBackground,
                        borderColor: theme.tint,
                      },
                    ]}
                  >
                    <ThemedText type="small">{label}</ThemedText>
                  </Pressable>
                ))}
              </View>
            </>
          ) : null}
          <ThemedText type="smallBold">截止时间</ThemedText>
          <View style={styles.optionRow}>
            {presets.map((preset) => (
              <Pressable
                key={preset.label}
                accessibilityRole="button"
                onPress={() => setDeadline(preset.value)}
                style={[
                  styles.option,
                  { borderColor: theme.backgroundSelected },
                  deadline === preset.value && {
                    backgroundColor: theme.tintBackground,
                    borderColor: theme.tint,
                  },
                ]}
              >
                <ThemedText type="small">{preset.label}</ThemedText>
              </Pressable>
            ))}
          </View>
          <View style={styles.actions}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="提交修改后的计划"
              disabled={busy}
              onPress={() => void resubmit()}
              style={[styles.primary, { backgroundColor: theme.tint, opacity: busy ? 0.6 : 1 }]}
            >
              {busy ? (
                <ActivityIndicator size="small" color={theme.backgroundElement} />
              ) : (
                <ThemedText type="smallBold" style={{ color: theme.backgroundElement }}>
                  按这版修改
                </ThemedText>
              )}
            </Pressable>
            <Pressable
              accessibilityRole="button"
              disabled={busy}
              onPress={() => setEditing(false)}
              style={[styles.secondary, { borderColor: theme.backgroundSelected }]}
            >
              <ThemedText type="smallBold" themeColor="textSecondary">
                收起
              </ThemedText>
            </Pressable>
          </View>
        </View>
      ) : null}
      {choiceRequired && proposal.status === 'pending' ? (
        <View style={[styles.editor, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="smallBold">个人基线还没就绪，请选一种方式</ThemedText>
          <View style={styles.optionRow}>
            {BASELINE_STRATEGY_OPTIONS.map((option) => (
              <Pressable
                key={option.value}
                accessibilityRole="button"
                accessibilityLabel={option.label}
                onPress={() => setStrategyChoice(option.value)}
                style={[
                  styles.option,
                  { borderColor: theme.backgroundSelected },
                  strategyChoice === option.value && {
                    backgroundColor: theme.tintBackground,
                    borderColor: theme.tint,
                  },
                ]}
              >
                <ThemedText type="small">{option.label}</ThemedText>
              </Pressable>
            ))}
          </View>
          <ThemedText type="small" themeColor="textSecondary">
            {BASELINE_STRATEGY_OPTIONS.find((option) => option.value === strategyChoice)?.hint ??
              '两种方式都用同一条已确认的截止时间；选「等基线」会在基线就绪后才开始比较。'}
          </ThemedText>
        </View>
      ) : null}
      {proposal.status === 'pending' ? (
        <View style={styles.actions}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="确认委托"
            disabled={busy}
            onPress={() => void decide('confirm')}
            style={[styles.primary, { backgroundColor: theme.tint, opacity: busy ? 0.6 : 1 }]}
          >
            {busy ? (
              <ActivityIndicator size="small" color={theme.backgroundElement} />
            ) : (
              <ThemedText type="smallBold" style={{ color: theme.backgroundElement }}>
                {fields.action === 'cancel' ? '确认取消' : '就这样，开始吧'}
              </ThemedText>
            )}
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={fields.action === 'cancel' ? '保留委托' : '不要'}
            disabled={busy}
            onPress={() => void decide('reject')}
            style={[styles.secondary, { borderColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold" themeColor="textSecondary">
              {fields.action === 'cancel' ? '先留着' : '不要'}
            </ThemedText>
          </Pressable>
        </View>
      ) : null}
      {!!error && (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}。候选还在，可以再试。
        </ThemedText>
      )}
      {editable ? (
        <Pressable
          accessibilityRole="button"
          onPress={() => setEditing(!editing)}
          style={styles.editToggle}
        >
          <ThemedText type="small" style={{ color: theme.tint }}>
            {editing ? '' : '想调整？改截止、阈值或基线选择'}
          </ThemedText>
        </Pressable>
      ) : null}
      {proposal.status === 'pending' ? (
        <ThemedText type="small" themeColor="textSecondary">
          更大的改动直接回复消息说明，它会重新出一版。确认后才开始观察。
        </ThemedText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderRadius: 12, padding: 10, gap: 8 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  headText: { flex: 1 },
  row: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  rowValue: { flex: 1, textAlign: 'right' },
  editor: { borderWidth: 1, borderRadius: 12, padding: 10, gap: 8 },
  optionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  option: { borderWidth: 1, borderRadius: 16, paddingHorizontal: 11, paddingVertical: 7 },
  thresholdInput: {
    borderWidth: 1,
    borderRadius: 12,
    minHeight: 44,
    paddingHorizontal: 12,
    width: 120,
    fontSize: 15,
  },
  actions: { flexDirection: 'row', gap: 8 },
  primary: {
    flex: 1,
    minHeight: 44,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  secondary: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 18,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  editToggle: { alignSelf: 'flex-start' },
});
