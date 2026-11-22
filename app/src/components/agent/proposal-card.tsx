/**
 * W03 确认提案卡（agent-w03-spec.md §4.2）：档案候选的确认 / 拒绝 / 已处理状态。
 * pending → 「确认保存 / 不要」；confirmed / rejected / superseded → 终态只读。
 * 决策为确定性代码：响应带刷新后的 turn，按 id 覆盖伙伴 store（卡片状态即回执）。
 * 修改候选走自然语言回复（D2），卡片底部保留引导。
 */

import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { decideAgentProposal, type AgentProposal } from '@/services/agent';
import { useAgentChatStore } from '@/stores/agent-chat';

/** 生日按精度渲染（D1）：2023-09-10 / 约 2023 年 9 月 / 约 2023 年。 */
function birthLabel(fields: AgentProposal['fields']): string | null {
  if (!fields.birthDate) return null;
  const precision = fields.birthDatePrecision ?? 'day';
  const [year, month, day] = fields.birthDate.split('-');
  if (precision === 'year') return `约 ${year} 年`;
  if (precision === 'month') return `约 ${year} 年 ${Number(month)} 月`;
  return `${year}-${month}-${day}`;
}

function proposalRows(fields: AgentProposal['fields']): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  if (fields.name) rows.push({ label: '名字', value: fields.name });
  if (fields.birthDate) rows.push({ label: '生日', value: birthLabel(fields) ?? '' });
  if (fields.breed != null && 'breed' in fields)
    rows.push({ label: '品种', value: fields.breed || '未填写' });
  if (fields.weightKg != null) {
    const date = fields.weightMeasuredOn;
    const raw = fields.weightRawText ? `（${fields.weightRawText}）` : '';
    rows.push({
      label: '体重',
      value: `${fields.weightKg} kg${raw}${date ? ` · ${date}` : ''}`,
    });
  }
  return rows;
}

export function ProposalCard({
  petId,
  proposal,
}: {
  petId: number | null;
  proposal: AgentProposal;
}) {
  const theme = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const applyTurn = useAgentChatStore((state) => state.applyTurn);

  const decide = async (action: 'confirm' | 'reject') => {
    if (busy || proposal.status !== 'pending' || petId == null) return;
    setBusy(true);
    setError('');
    try {
      const result = await decideAgentProposal(petId, proposal.id, action);
      if (result.turn) applyTurn(result.turn); // 决策后服务端重写 blocks 并推 turn；本地按 id 覆盖
    } catch (exc) {
      setError(exc instanceof Error && exc.message ? exc.message : '操作失败，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  const rows = proposalRows(proposal.fields);
  const statusText =
    proposal.status === 'confirmed'
      ? '已写入档案'
      : proposal.status === 'rejected'
        ? '已取消'
        : proposal.status === 'superseded'
          ? '已有新候选'
          : null;

  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.head}>
        <SymbolView
          name={{ ios: 'square.and.pencil', android: 'edit_note', web: 'edit_note' }}
          tintColor={theme.tint}
          size={16}
        />
        <ThemedText type="smallBold" style={styles.headText}>
          档案修改建议{proposal.version > 1 ? `（第 ${proposal.version} 版）` : ''}
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
      {proposal.status === 'pending' ? (
        <View style={styles.actions}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="确认保存档案修改"
            disabled={busy}
            onPress={() => void decide('confirm')}
            style={[styles.primary, { backgroundColor: theme.tint, opacity: busy ? 0.6 : 1 }]}
          >
            {busy ? (
              <ActivityIndicator size="small" color={theme.backgroundElement} />
            ) : (
              <ThemedText type="smallBold" style={{ color: theme.backgroundElement }}>
                确认保存
              </ThemedText>
            )}
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="拒绝这次档案修改"
            disabled={busy}
            onPress={() => void decide('reject')}
            style={[styles.secondary, { borderColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold" themeColor="textSecondary">
              不要
            </ThemedText>
          </Pressable>
        </View>
      ) : null}
      {!!error && (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {error}。候选还在，可以再试。
        </ThemedText>
      )}
      {proposal.status === 'pending' ? (
        <ThemedText type="small" themeColor="textSecondary">
          想改？直接回复消息说明，它会重新给你一版。
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
});
