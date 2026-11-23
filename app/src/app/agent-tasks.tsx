/**
 * W08 委托簿（agent-w08-spec.md §2）：状态、当前计划、提醒与回信计数；暂停/恢复/取消
 * 直接生效（幂等，失败按响应提示）；「回到原会话」设置伙伴页主题后返回（深链）。
 * 回信正文与来源下钻在伙伴会话回合里（task_result 块 + 观测来源卡）。
 */

import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { SymbolView, type AndroidSymbol, type SFSymbol } from 'expo-symbols';
import { router } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { MaxContentWidth, Shadows } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchDashboard } from '@/services/api';
import { agentTaskOp, fetchAgentTasks, type AgentTask } from '@/services/agent';
import { useAgentChatStore } from '@/stores/agent-chat';

type MultiPlatformIcon = { ios: SFSymbol; android: AndroidSymbol; web: AndroidSymbol };

const KIND_ICONS: Record<AgentTask['kind'], MultiPlatformIcon> = {
  fact_watch: { ios: 'eye', android: 'visibility', web: 'visibility' },
  comparison: { ios: 'chart.bar', android: 'bar_chart', web: 'bar_chart' },
  scheduled_review: { ios: 'clock', android: 'schedule', web: 'schedule' },
};

const CLOCK_ICON: MultiPlatformIcon = {
  ios: 'clock.arrow.circlepath',
  android: 'schedule',
  web: 'schedule',
};

const CHEVRON_ICON: MultiPlatformIcon = {
  ios: 'chevron.right',
  android: 'chevron_right',
  web: 'chevron_right',
};

function formatWhen(iso: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

function conditionText(task: AgentTask): string | null {
  if (!task.condition) return null;
  const unit = task.condition.metric === 'walkSeconds' ? '分钟' : '次';
  const value =
    task.condition.metric === 'walkSeconds'
      ? Math.round(task.condition.value / 60)
      : task.condition.value;
  const direction = task.condition.op === 'at_least' ? '达到或超过' : '不多于';
  const scope = task.condition.scope === 'daily' ? '单日' : '累计';
  return `${scope}${task.featureLabel ?? ''} ${direction} ${value} ${unit} 时提醒`;
}

/** 状态胶囊配色：进行中绿、已交付珊瑚橙、等待基线琥珀、暂停/取消中性 */
function statusPill(
  status: AgentTask['status'],
  theme: ReturnType<typeof useTheme>,
): { fg: string; bg: string } {
  if (status === 'observing') return { fg: theme.onlineForeground, bg: theme.onlineBackground };
  if (status === 'completed') return { fg: theme.tintForeground, bg: theme.tintBackground };
  if (status === 'waiting_baseline')
    return { fg: theme.warningForeground, bg: theme.warningBackground };
  return { fg: theme.text, bg: theme.backgroundSelected };
}

function TaskRow({
  task,
  petId,
  onChanged,
}: {
  task: AgentTask;
  petId: number;
  onChanged: () => void;
}) {
  const theme = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // 截止判断只在首渲染取一次当前时间，避免 render 里反复调用不纯函数
  const [now] = useState(() => Date.now());

  const run = async (op: 'pause' | 'resume' | 'cancel') => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await agentTaskOp(petId, task.id, op);
      onChanged();
    } catch (exc) {
      const message = exc instanceof Error && exc.message ? exc.message : '操作失败';
      setError(message); // 409 的服务端消息说明当前状态，如实展示
    } finally {
      setBusy(false);
    }
  };

  const pill = statusPill(task.status, theme);
  const overdue = task.status === 'observing' && now > new Date(task.deadlineAt).getTime();
  const deadlineColor = overdue ? theme.warningForeground : theme.textSecondary;

  return (
    <View
      style={[
        styles.task,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.taskHead}>
        <SymbolView name={KIND_ICONS[task.kind] ?? CLOCK_ICON} tintColor={theme.tint} size={16} />
        <ThemedText type="default" style={styles.taskTitle}>
          {task.title}
        </ThemedText>
        <View style={[styles.statusPill, { backgroundColor: pill.bg }]}>
          <ThemedText type="small" style={{ color: pill.fg }}>
            {task.statusLabel}
          </ThemedText>
        </View>
      </View>
      <View style={styles.metaRow}>
        <ThemedText type="small" themeColor="textSecondary">
          {task.kindLabel}
          {task.featureLabel ? ` · ${task.featureLabel}` : ''}
        </ThemedText>
        <View style={styles.deadline}>
          <SymbolView name={CLOCK_ICON} tintColor={deadlineColor} size={11} />
          <ThemedText type="small" style={{ color: deadlineColor }}>
            {overdue ? '已过截止 ' : '截止 '}
            {formatWhen(task.deadlineAt)}
          </ThemedText>
        </View>
      </View>
      <ThemedText type="default">{task.question}</ThemedText>
      {conditionText(task) ? (
        <ThemedText type="small" themeColor="textSecondary">
          {conditionText(task)}
        </ThemedText>
      ) : null}
      <View style={[styles.divider, { backgroundColor: theme.backgroundSelected }]} />
      <View style={styles.footerRow}>
        <ThemedText type="small" themeColor="textSecondary">
          提醒 {task.reminders} · 回信 {task.letters}
          {task.currentVersion > 1 ? ` · 第 ${task.currentVersion} 版` : ''}
        </ThemedText>
        {task.topicId ? (
          <Pressable
            accessibilityRole="link"
            onPress={() => {
              useAgentChatStore.getState().setTopic(task.topicId ?? undefined);
              router.back();
            }}
            style={styles.topicLink}
          >
            <ThemedText type="small" themeColor="tintForeground">
              回到原会话
            </ThemedText>
            <SymbolView name={CHEVRON_ICON} tintColor={theme.tint} size={11} />
          </Pressable>
        ) : null}
      </View>
      {task.status !== 'completed' && task.status !== 'cancelled' ? (
        <View style={styles.ops}>
          {task.status === 'paused' ? (
            <Pressable
              accessibilityRole="button"
              disabled={busy}
              onPress={() => void run('resume')}
              style={[
                styles.op,
                styles.opPrimary,
                { borderColor: theme.tint, backgroundColor: theme.tintBackground },
              ]}
            >
              <ThemedText type="smallBold" themeColor="tintForeground">
                恢复观察
              </ThemedText>
            </Pressable>
          ) : (
            <Pressable
              accessibilityRole="button"
              disabled={busy}
              onPress={() => void run('pause')}
              style={[styles.op, { borderColor: theme.backgroundSelected }]}
            >
              <ThemedText type="smallBold" themeColor="textSecondary">
                暂停
              </ThemedText>
            </Pressable>
          )}
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            onPress={() => void run('cancel')}
            style={[styles.op, { borderColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold" themeColor="textSecondary">
              取消委托
            </ThemedText>
          </Pressable>
        </View>
      ) : null}
      {!!error && (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}
        </ThemedText>
      )}
    </View>
  );
}

export default function AgentTasksScreen() {
  const theme = useTheme();
  const { user } = useAuth();
  const [petId, setPetId] = useState<number | null>(null);
  const [tasks, setTasks] = useState<AgentTask[] | null>(null);
  const [error, setError] = useState('');

  const reload = useCallback(async () => {
    if (!user || petId == null) return;
    try {
      const data = await fetchAgentTasks(petId);
      setTasks(data.tasks);
      setError('');
    } catch {
      setError('委托簿暂时没能取回，点这里重试');
    }
  }, [user, petId]);

  useEffect(() => {
    if (!user) return;
    let active = true;
    void fetchDashboard()
      .then((data) => {
        if (active && data.pet) setPetId(data.pet.id);
      })
      .catch(() => {
        if (active) setError('暂时没能取回宠物信息');
      });
    return () => {
      active = false;
    };
  }, [user]);

  useEffect(() => {
    void Promise.resolve().then(reload);
  }, [reload]);

  if (!user) {
    return (
      <View style={[styles.container, styles.center, { backgroundColor: theme.background }]}>
        <ThemedText type="small" themeColor="textSecondary">
          登录后可以查看你的委托。
        </ThemedText>
      </View>
    );
  }

  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={[styles.container, !tasks?.length && styles.center]}
      contentInsetAdjustmentBehavior="automatic"
    >
      {error ? (
        <Pressable accessibilityRole="button" onPress={() => void reload()} style={styles.error}>
          <ThemedText type="small" themeColor="danger">
            {error}
          </ThemedText>
        </Pressable>
      ) : null}
      {tasks == null && !error ? <ActivityIndicator size="small" color={theme.tint} /> : null}
      {tasks && !tasks.length && !error ? (
        <View style={styles.empty}>
          <SymbolView
            name={{ ios: 'checklist', android: 'checklist', web: 'checklist' }}
            tintColor={theme.textSecondary}
            size={32}
          />
          <ThemedText type="smallBold">还没有委托</ThemedText>
          <ThemedText type="small" themeColor="textSecondary" style={styles.emptyHint}>
            在伙伴会话里说「帮我留意…」，确认后就会出现在这里。
          </ThemedText>
        </View>
      ) : null}
      {tasks?.map((task) => (
        <TaskRow
          key={task.id}
          task={task}
          petId={petId as number}
          onChanged={() => void reload()}
        />
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    padding: 18,
    gap: 14,
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
  },
  center: { justifyContent: 'center' },
  error: { alignSelf: 'center', paddingVertical: 4 },
  empty: { alignItems: 'center', gap: 10, paddingBottom: 24 },
  emptyHint: { maxWidth: 260, textAlign: 'center' },
  task: {
    borderWidth: 1,
    borderRadius: 16,
    padding: 14,
    gap: 10,
    boxShadow: Shadows.card,
  },
  taskHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  taskTitle: { flex: 1, fontWeight: '600' },
  statusPill: {
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  deadline: { flexDirection: 'row', alignItems: 'center', gap: 3, marginLeft: 'auto' },
  divider: { height: StyleSheet.hairlineWidth },
  footerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  topicLink: { flexDirection: 'row', alignItems: 'center', gap: 2, paddingVertical: 2 },
  ops: { flexDirection: 'row', gap: 8 },
  op: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    minHeight: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
  opPrimary: { paddingHorizontal: 16 },
});
