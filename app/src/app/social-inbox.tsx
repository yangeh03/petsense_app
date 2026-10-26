import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import {
  fetchSocialNotifications,
  markNotificationRead,
  markNotificationsReadThrough,
} from '@/services/api';
import type { SocialNotification } from '@/types/social';

export default function SocialInboxScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const viewerId = user?.id ?? 0;
  const [items, setItems] = useState<SocialNotification[]>([]);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const sequence = useRef(0);
  const [unread, setUnread] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const current = ++sequence.current;
    if (!user) {
      setItems([]);
      setUnread(0);
      setCursor(null);
      setLoadedFor(0);
      setLoading(false);
      return;
    }
    try {
      const data = await fetchSocialNotifications();
      if (current !== sequence.current) return;
      setItems(data.notifications);
      setLoadedFor(viewerId);
      setUnread(data.unreadCount);
      setCursor(data.page.nextCursor);
      setError('');
    } catch (exc) {
      if (current !== sequence.current) return;
      setItems([]);
      setLoadedFor(viewerId);
      setError(exc instanceof Error ? exc.message : '通知加载失败，请重试');
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [user, viewerId]);

  useFocusEffect(
    useCallback(() => {
      void load();
      return () => {
        sequence.current++;
      };
    }, [load]),
  );

  const openItem = async (item: SocialNotification) => {
    try {
      if (!item.readAt) {
        const data = await markNotificationRead(item.id);
        setUnread(data.unreadCount);
        setItems((current) =>
          current.map((row) =>
            row.id === item.id ? { ...row, readAt: new Date().toISOString() } : row,
          ),
        );
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '标记已读失败');
    }
    if (item.postId && item.targetAvailable) router.push(`/post?id=${item.postId}`);
    else if (item.kind === 'follow') router.push(`/user?id=${item.actorId}`);
  };

  const readAll = async () => {
    if (!items.length || loadedFor !== viewerId) return;
    try {
      const data = await markNotificationsReadThrough(items[0].id);
      setUnread(data.unreadCount);
      setItems((current) =>
        current.map((row) => ({ ...row, readAt: row.readAt ?? new Date().toISOString() })),
      );
      setError('');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '标记已读失败');
    }
  };

  const loadMore = async () => {
    if (!cursor || loading || loadedFor !== viewerId) return;
    setLoading(true);
    try {
      const data = await fetchSocialNotifications(cursor);
      setItems((current) => [...current, ...data.notifications]);
      setCursor(data.page.nextCursor);
      setUnread(data.unreadCount);
      setError('');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载更多失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <View style={styles.content}>
        <View style={styles.header}>
          <Pressable onPress={() => router.back()} accessibilityLabel="返回社区">
            <SymbolView
              name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
              size={24}
              tintColor={theme.tint}
            />
          </Pressable>
          <ThemedText type="subtitle">
            互动通知{loadedFor === viewerId && unread ? ` · ${unread}` : ''}
          </ThemedText>
          <Pressable onPress={() => void readAll()} accessibilityLabel="全部标记已读">
            <ThemedText type="small" style={{ color: theme.tint }}>
              全部已读
            </ThemedText>
          </Pressable>
        </View>
        {!user ? (
          <Pressable onPress={() => router.push('/login')}>
            <ThemedText type="small" style={{ color: theme.tint }}>
              登录后查看互动通知
            </ThemedText>
          </Pressable>
        ) : loading && items.length === 0 ? (
          <ActivityIndicator color={theme.tint} />
        ) : null}
        {!!error && (
          <Pressable onPress={() => void load()}>
            <ThemedText type="small" style={{ color: theme.danger }}>
              {error} · 点击重试
            </ThemedText>
          </Pressable>
        )}
        <FlatList
          data={loadedFor === viewerId ? items : []}
          keyExtractor={(item) => String(item.id)}
          ListEmptyComponent={
            !loading && user && !error ? (
              <ThemedText type="small" themeColor="textSecondary">
                暂无互动通知
              </ThemedText>
            ) : null
          }
          renderItem={({ item }) => (
            <Pressable
              onPress={() => void openItem(item)}
              style={[
                styles.row,
                {
                  borderColor: theme.backgroundSelected,
                  backgroundColor: item.readAt ? theme.background : theme.tintBackground,
                },
              ]}
            >
              <ThemedText type="smallBold">
                {item.actorEmoji} {item.actorName}
              </ThemedText>
              <ThemedText type="small">
                {
                  {
                    like: '赞了你的帖子',
                    comment: '评论了你的帖子',
                    reply: '回复了你的评论',
                    follow: '关注了你',
                  }[item.kind]
                }
              </ThemedText>
              {!item.targetAvailable && (
                <ThemedText type="small" themeColor="textSecondary">
                  原内容已不可用
                </ThemedText>
              )}
              <ThemedText type="small" themeColor="textSecondary">
                {new Date(item.createdAt).toLocaleString()}
              </ThemedText>
            </Pressable>
          )}
          ListFooterComponent={
            cursor ? (
              <Pressable onPress={() => void loadMore()}>
                <ThemedText type="small" style={{ color: theme.tint }}>
                  加载更多
                </ThemedText>
              </Pressable>
            ) : null
          }
        />
      </View>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: {
    flex: 1,
    padding: Spacing.four,
    gap: Spacing.three,
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  row: { borderBottomWidth: 1, padding: Spacing.three, gap: Spacing.one },
});
