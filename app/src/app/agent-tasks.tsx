/**
 * W08 委托簿（agent-w08-spec.md §2）：状态、当前计划、提醒与回信计数；暂停/恢复/取消
 * 直接生效（幂等，失败按响应提示）；「回到原会话」设置伙伴页主题后返回（深链）。
 * 回信正文与来源下钻在伙伴会话回合里（task_result 块 + 观测来源卡）。
 */

import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';
import { router } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { MaxContentWidth } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchDashboard } from '@/services/api';
import { agentTaskOp, fetchAgentTasks, type AgentTask } from '@/services/agent';
import { useAgentChatStore } from '@/stores/agent-chat';

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

function statusColor(task: AgentTask, theme: ReturnType<typeof useTheme>): string {
  if (task.status === 'observing') return theme.online;
  if (task.status === 'completed') return theme.tint;
  return theme.textSecondary;
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

  return (
    <View
      style={[
        styles.task,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.taskHead}>
        <View style={styles.taskTitleRow}>
          <ThemedText type="smallBold" style={styles.taskTitle}>
            {task.title}
          </ThemedText>
          <ThemedText type="small" style={{ color: statusColor(task, theme) }}>
            {task.statusLabel}
          </ThemedText>
        </View>
        <ThemedText type="small" themeColor="textSecondary">
          {task.kindLabel}
          {task.featureLabel ? ` · ${task.featureLabel}` : ''} · 截止 {formatWhen(task.deadlineAt)}
        </ThemedText>
      </View>
      <ThemedText type="small">{task.question}</ThemedText>
      {conditionText(task) ? (
        <ThemedText type="small" themeColor="textSecondary">
          {conditionText(task)}
        </ThemedText>
      ) : null}
      <View style={styles.metaRow}>
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
            <ThemedText type="small" style={{ color: theme.tint }}>
              回到原会话
            </ThemedText>
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
              style={[styles.op, { borderColor: theme.tint }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.tint }}>
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
          <ThemedText type="small" themeColor="textSecondary">
            还没有委托。在伙伴会话里说「帮我留意…」，确认后就会出现在这里。
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
  task: { borderWidth: 1, borderRadius: 14, padding: 12, gap: 8 },
  taskHead: { gap: 2 },
  taskTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  taskTitle: { flex: 1 },
  metaRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  topicLink: { paddingVertical: 2 },
  ops: { flexDirection: 'row', gap: 8 },
  op: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    minHeight: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
