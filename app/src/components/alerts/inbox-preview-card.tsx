/**
 * W12 今日预览卡：今日最多一条未读预览（agent-w12-spec.md D2）。
 *
 * 预览条目与铃铛共用服务端列表、计数与已读语义；点击走统一深链解析页，
 * 目标成功显示后才自动标已读。
 */

import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useAgentInbox } from '@/hooks/use-agent-inbox';
import { useTheme } from '@/hooks/use-theme';
import { INBOX_SOURCE_LABEL } from '@/services/agent-inbox';

export function InboxPreviewCard({ petId }: { petId?: number }) {
  const theme = useTheme();
  const router = useRouter();
  const { preview } = useAgentInbox(petId);

  if (!preview) return null;
  const color =
    preview.sourceKind === 'device_fact'
      ? theme.danger
      : preview.sourceKind === 'reply'
        ? theme.tint
        : theme.info;

  return (
    <Pressable
      onPress={() => router.push(`/inbox/${preview.id}`)}
      style={[styles.card, { backgroundColor: theme.backgroundElement }]}
      accessibilityLabel={`未读通知 ${preview.title}`}
    >
      <View style={[styles.iconDot, { backgroundColor: `${color}1F` }]}>
        <SymbolView
          name={{ ios: 'bell.badge.fill', android: 'notifications', web: 'notifications' }}
          size={16}
          tintColor={color}
        />
      </View>
      <View style={styles.texts}>
        <ThemedText type="smallBold" numberOfLines={1}>
          {preview.title}
        </ThemedText>
        <ThemedText type="small" themeColor="textSecondary" numberOfLines={2}>
          {INBOX_SOURCE_LABEL[preview.sourceKind]} · {preview.summary || '点开查看详情'}
        </ThemedText>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    borderRadius: 16,
    padding: Spacing.three,
    marginBottom: Spacing.three,
  },
  iconDot: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  texts: { flex: 1, gap: 2 },
});
