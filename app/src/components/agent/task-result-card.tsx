/**
 * W08 任务结果卡（后台交付块 task_result）：委托提醒/回信的结构化卡片。
 * 正文由同回合的 text 块承载（旧客户端可读降级），本卡提供类型、截止与下钻入口。
 */

import { Pressable, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import type { AgentTaskResultBlock } from '@/services/agent';
import { evidenceRows } from '@/services/agent-change';

const KIND_LABELS: Record<AgentTaskResultBlock['kind'], string> = {
  reminder: '委托提醒',
  letter: '委托回信',
  degraded_letter: '委托回信（未完成评估）',
};
const PLAN_KIND_LABELS: Record<AgentTaskResultBlock['planKind'], string> = {
  fact_watch: '事实观察',
  comparison: '个人比较',
  scheduled_review: '定时回顾',
};

function formatDeadline(iso: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

export function TaskResultCard({ block }: { block: AgentTaskResultBlock }) {
  const theme = useTheme();
  const router = useRouter();
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.head}>
        <SymbolView
          name={{ ios: 'checkmark.seal', android: 'verified', web: 'verified' }}
          tintColor={block.kind === 'reminder' ? theme.tint : theme.online}
          size={16}
        />
        <View style={styles.headBody}>
          <ThemedText type="smallBold">
            {KIND_LABELS[block.kind]}
            {block.title ? `：${block.title}` : ''}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {PLAN_KIND_LABELS[block.planKind]} · 第 {block.version} 版 · 截止{' '}
            {formatDeadline(block.deadlineAt)}
          </ThemedText>
        </View>
      </View>
      {block.degraded ? (
        <ThemedText type="small" themeColor="textSecondary">
          这封回信未完成评估，只包含覆盖与缺口说明，不代表它的状态正常或异常。
        </ThemedText>
      ) : null}
      {(block.changeEvidence ?? []).map((evidence) => (
        <View key={evidence.evidenceId} style={styles.evidence}>
          <ThemedText type="smallBold">
            {evidence.dimension ?? '个人变化'}
            {evidence.slotLabel ? ` · ${evidence.slotLabel}` : ''} · {evidence.date}
          </ThemedText>
          {evidenceRows(evidence).map((row) => (
            <View key={row.label} style={styles.row}>
              <ThemedText type="small" themeColor="textSecondary">
                {row.label}
              </ThemedText>
              <ThemedText type="small" style={styles.rowValue}>
                {row.value}
              </ThemedText>
            </View>
          ))}
        </View>
      ))}
      <Pressable
        accessibilityRole="link"
        onPress={() => router.push('/agent-tasks')}
        style={styles.link}
      >
        <ThemedText type="small" style={{ color: theme.tint }}>
          在委托簿查看这项委托
        </ThemedText>
        <SymbolView
          name={{ ios: 'arrow.up.right', android: 'north_east', web: 'north_east' }}
          tintColor={theme.tint}
          size={12}
        />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  evidence: { gap: 4 },
  row: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  rowValue: { flex: 1, textAlign: 'right' },
  card: { borderWidth: 1, borderRadius: 12, padding: 10, gap: 8 },
  head: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  headBody: { flex: 1, gap: 2 },
  link: { flexDirection: 'row', alignItems: 'center', gap: 4 },
});
