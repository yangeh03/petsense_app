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
import { fetchBookmarks, setBookmark } from '@/services/api';
import type { Post } from '@/types/social';

export default function BookmarksScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const viewerId = user?.id ?? 0;
  const [posts, setPosts] = useState<Post[]>([]);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const sequence = useRef(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const current = ++sequence.current;
    if (!user) {
      setPosts([]);
      setCursor(null);
      setLoadedFor(0);
      setLoading(false);
      return;
    }
    try {
      const data = await fetchBookmarks();
      if (current !== sequence.current) return;
      setPosts(data.posts);
      setLoadedFor(viewerId);
      setCursor(data.page.nextCursor);
      setError('');
    } catch (exc) {
      if (current !== sequence.current) return;
      setLoadedFor(viewerId);
      setPosts([]);
      setError(exc instanceof Error ? exc.message : '收藏加载失败，请重试');
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

  const loadMore = async () => {
    if (!cursor || loading || loadedFor !== viewerId) return;
    setLoading(true);
    try {
      const data = await fetchBookmarks(cursor);
      setPosts((current) => [...current, ...data.posts]);
      setCursor(data.page.nextCursor);
      setError('');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '更多收藏加载失败');
    } finally {
      setLoading(false);
    }
  };

  const remove = async (postId: number) => {
    try {
      await setBookmark(postId, false);
      setPosts((current) => current.filter((item) => item.id !== postId));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '取消收藏失败，请重试');
    }
  };

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <View style={styles.content}>
        <View style={styles.header}>
          <Pressable onPress={() => router.back()} accessibilityLabel="返回我的">
            <SymbolView
              name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
              size={24}
              tintColor={theme.tint}
            />
          </Pressable>
          <ThemedText type="subtitle">我的收藏</ThemedText>
          <View style={styles.spacer} />
        </View>
        {!!error && (
          <Pressable onPress={() => void load()}>
            <ThemedText type="small" style={{ color: theme.danger }}>
              {error} · 点击重试
            </ThemedText>
          </Pressable>
        )}
        {(loading || loadedFor !== viewerId) && <ActivityIndicator color={theme.tint} />}
        <FlatList
          data={loadedFor === viewerId ? posts : []}
          keyExtractor={(item) => String(item.id)}
          ListEmptyComponent={
            !loading && !error ? (
              <ThemedText type="small" themeColor="textSecondary">
                还没有收藏的帖子
              </ThemedText>
            ) : undefined
          }
          renderItem={({ item }) => (
            <View style={[styles.row, { borderColor: theme.backgroundSelected }]}>
              <Pressable onPress={() => router.push(`/post?id=${item.id}`)} style={styles.summary}>
                <ThemedText type="smallBold">
                  {item.avatar_emoji} {item.display_name}
                </ThemedText>
                <ThemedText type="small" numberOfLines={2}>
                  {item.caption || '图片动态'}
                </ThemedText>
              </Pressable>
              <Pressable onPress={() => void remove(item.id)} accessibilityLabel="取消收藏">
                <SymbolView
                  name={{ ios: 'bookmark.fill', android: 'bookmark', web: 'bookmark' }}
                  size={22}
                  tintColor={theme.tint}
                />
              </Pressable>
            </View>
          )}
          ListFooterComponent={
            cursor ? (
              <Pressable onPress={() => void loadMore()}>
                <ThemedText type="small" style={{ color: theme.tint }}>
                  加载更多
                </ThemedText>
              </Pressable>
            ) : undefined
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
  spacer: { width: 24 },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottomWidth: 1,
    paddingVertical: Spacing.three,
  },
  summary: { flex: 1, gap: Spacing.one },
});
