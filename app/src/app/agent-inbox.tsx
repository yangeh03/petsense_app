/**
 * W12 收件箱页：稳定来源列表、单条/全部已读（agent-w12-spec.md）。
 *
 * 列表与计数来自服务端（铃铛与今日预览同源）；点击条目走统一深链解析页
 * `/inbox/<id>`，目标成功显示后才自动标为已读。
 */

import { SymbolView } from 'expo-symbols';
import { Stack, useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useAgentInbox } from '@/hooks/use-agent-inbox';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { INBOX_SOURCE_LABEL } from '@/services/agent-inbox';

export default function AgentInboxScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { items, unreadCount, loading, error, refresh, markAllRead } = useAgentInbox();
  const { user } = useAuth();
  const signedIn = user != null;

  return (
    <View style={[styles.screen, { backgroundColor: theme.background }]}>
      <Stack.Screen
        options={{
          headerShown: true,
          title: '收件箱',
          headerBackTitle: '返回',
          headerBackButtonDisplayMode: 'minimal',
          headerLeft: !router.canGoBack()
            ? () => (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="返回今日"
                  onPress={() => router.replace('/')}
                  style={styles.backAction}
                >
                  <SymbolView
                    name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
                    size={22}
                    tintColor={theme.text}
                  />
                </Pressable>
              )
            : undefined,
          headerRight:
            unreadCount > 0
              ? () => (
                  <Pressable
                    onPress={() => void markAllRead()}
                    accessibilityRole="button"
                    accessibilityLabel="全部标为已读"
                    style={[styles.action, { backgroundColor: theme.tintBackground }]}
                  >
                    <Text style={[styles.actionText, { color: theme.tintForeground }]}>
                      全部已读
                    </Text>
                  </Pressable>
                )
              : undefined,
        }}
      />
      <ScrollView
        contentInsetAdjustmentBehavior="automatic"
        contentContainerStyle={[styles.list, { paddingBottom: insets.bottom + Spacing.four }]}
        refreshControl={undefined}
      >
        {!signedIn ? (
          <Pressable onPress={() => router.push('/login')} accessibilityRole="button">
            <ThemedText type="small" style={{ color: theme.tintForeground }}>
              登录后查看通知
            </ThemedText>
          </Pressable>
        ) : null}
        {signedIn && loading && items.length === 0 ? (
          <ThemedText type="small" themeColor="textSecondary">
            正在读取…
          </ThemedText>
        ) : null}
        {error ? (
          <Pressable onPress={() => void refresh()} accessibilityRole="button">
            <ThemedText type="small" themeColor="textSecondary">
              {error}（点击重试）
            </ThemedText>
          </Pressable>
        ) : null}
        {/* 读失败时不能同时说「没有通知」：空态只在确实读到空列表时出现 */}
        {signedIn && !loading && !error && items.length === 0 ? (
          <View style={styles.empty}>
            <Text style={styles.emptyEmoji}>🐾</Text>
            <ThemedText type="small" themeColor="textSecondary">
              暂时没有通知
            </ThemedText>
          </View>
        ) : null}
        {items.map((item) => {
          const unread = !item.readAt;
          const color =
            item.sourceKind === 'device_fact'
              ? theme.danger
              : item.sourceKind === 'reply'
                ? theme.tint
                : theme.info;
          // 来源类别是文字，要用压白底达标的变体；图标与圆点仍用实底品牌色
          const kindTextColor =
            item.sourceKind === 'device_fact'
              ? theme.danger
              : item.sourceKind === 'reply'
                ? theme.tintForeground
                : theme.infoForeground;
          return (
            <Pressable
              key={item.id}
              onPress={() => router.push(`/inbox/${item.id}`)}
              style={[styles.row, { backgroundColor: theme.backgroundElement }]}
              accessibilityLabel={`${INBOX_SOURCE_LABEL[item.sourceKind]} ${item.title}`}
            >
              <View style={[styles.iconDot, { backgroundColor: `${color}1F` }]}>
                <SymbolView
                  name={{ ios: 'bell.fill', android: 'notifications', web: 'notifications' }}
                  size={16}
                  tintColor={color}
                />
              </View>
              <View style={styles.texts}>
                <View style={styles.titleRow}>
                  <Text style={[styles.kind, { color: kindTextColor }]}>
                    {INBOX_SOURCE_LABEL[item.sourceKind]}
                  </Text>
                  <ThemedText type="smallBold" style={styles.title} numberOfLines={1}>
                    {item.title}
                  </ThemedText>
                </View>
                {item.summary ? (
                  <ThemedText type="small" themeColor="textSecondary" numberOfLines={2}>
                    {item.summary}
                  </ThemedText>
                ) : null}
                <ThemedText type="small" themeColor="textSecondary">
                  {item.readAt ? '已读' : '未读'} · {new Date(item.createdAt).toLocaleString()}
                </ThemedText>
              </View>
              {unread ? <View style={[styles.dot, { backgroundColor: color }]} /> : null}
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  backAction: { minWidth: 44, minHeight: 44, justifyContent: 'center' },
  action: {
    borderRadius: 999,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two,
    minHeight: 44,
    justifyContent: 'center',
  },
  actionText: { fontSize: 13, fontWeight: '600' },
  list: { paddingHorizontal: Spacing.four, paddingTop: Spacing.three, gap: Spacing.two },
  empty: { alignItems: 'center', gap: Spacing.two, paddingVertical: Spacing.six },
  emptyEmoji: { fontSize: 34 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    borderRadius: 16,
    padding: Spacing.three,
  },
  iconDot: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  texts: { flex: 1, gap: 2 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  kind: { fontSize: 11, fontWeight: '700' },
  title: { flex: 1 },
  dot: { width: 8, height: 8, borderRadius: 4 },
});
