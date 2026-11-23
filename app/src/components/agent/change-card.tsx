/**
 * W11 个人变化卡（personal_change 块）：冻结证据的可展开展示。
 *
 * 服务端在交付时冻结样本窗口、基线版本与修订号、分位数、实际值与方向（D6），
 * 本卡按原证据展示，不重算比较；旧客户端无此块时按 blocks 内 text 块可读降级。
 */

import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { evidenceRows, type AgentChangeEvidence } from '@/services/agent-change';

export function ChangeCard({ change }: { change: AgentChangeEvidence }) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const rows = evidenceRows(change);
  const direction = change.direction;
  const tint =
    direction === 'above' ? theme.tint : direction === 'below' ? theme.warning : theme.text;

  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.head}>
        <SymbolView
          name={{ ios: 'chart.line.uptrend.xyaxis', android: 'show_chart', web: 'show_chart' }}
          tintColor={tint}
          size={16}
        />
        <View style={styles.headBody}>
          <ThemedText type="smallBold">
            {change.dimension ?? '个人变化'}
            {change.slotLabel ? ` · ${change.slotLabel}` : ''}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {change.date} · 实际 {rows.find((row) => row.label === '实际')?.value ?? '无数据'} ·{' '}
            {rows.find((row) => row.label === '对比')?.value ?? ''}
          </ThemedText>
        </View>
      </View>
      {expanded ? (
        <View style={styles.rows}>
          {rows.map((row) => (
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
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={expanded ? '收起证据' : '展开证据'}
        onPress={() => setExpanded(!expanded)}
        style={styles.toggle}
      >
        <ThemedText type="small" style={{ color: theme.tint }}>
          {expanded ? '收起证据' : '为什么这么说？看证据'}
        </ThemedText>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderRadius: 12, padding: 10, gap: 8 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  headBody: { flex: 1, gap: 2 },
  rows: { gap: 4 },
  row: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  rowValue: { flex: 1, textAlign: 'right' },
  toggle: { alignSelf: 'flex-start' },
});
