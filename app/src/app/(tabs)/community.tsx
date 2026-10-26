import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CommentSheet } from '@/components/social/comment-sheet';
import { PostCard } from '@/components/social/post-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, Fonts, MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { usePetChanges } from '@/hooks/use-pet-changes';
import { useTheme } from '@/hooks/use-theme';
import {
  deletePost,
  fetchFeed,
  fetchSocialNotifications,
  fetchStoryTray,
  setBookmark,
  setLike,
} from '@/services/api';
import type { Post, StoryGroup } from '@/types/social';

type Scope = 'discover' | 'following';

export default function CommunityScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [scope, setScope] = useState<Scope>('discover');
  const [posts, setPosts] = useState<Post[]>([]);
  const [stories, setStories] = useState<StoryGroup[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [storyError, setStoryError] = useState('');
  const [pageError, setPageError] = useState('');
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [commentsFor, setCommentsFor] = useState<Post | null>(null);
  const [loadedKey, setLoadedKey] = useState('');
  const sequence = useRef(0);
  const pending = useRef(new Set<string>());
  const currentKey = `${user?.id ?? 0}:${scope}`;
  const visiblePosts = loadedKey === currentKey ? posts : [];
  const visibleStories = loadedKey === currentKey ? stories : [];

  const load = useCallback(async () => {
    const current = ++sequence.current;
    // 故事环只是头部装饰内容，加载失败不能连坐整个 Feed。
    void fetchStoryTray()
      .then((tray) => {
        if (current !== sequence.current) return;
        setStories(tray.groups);
        setStoryError('');
      })
      .catch((exc) => {
        if (current !== sequence.current) return;
        setStoryError(exc instanceof Error ? exc.message : '故事加载失败');
      });
    try {
      const feed = await fetchFeed(undefined, { limit: 20, scope });
      if (current !== sequence.current) return;
      setPosts(feed.posts);
      setLoadedKey(currentKey);
      setUnread(0);
      setNextCursor(feed.page.nextCursor);
      setError('');
      setPageError('');
      if (user) {
        void fetchSocialNotifications()
          .then((data) => {
            if (current === sequence.current) setUnread(data.unreadCount);
          })
          .catch(() => {});
      }
    } catch (exc) {
      if (current !== sequence.current) return;
      setLoadedKey(currentKey);
      setError(exc instanceof Error ? exc.message : '社区动态加载失败，请重试');
    } finally {
      if (current === sequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [scope, user, currentKey]);

  useFocusEffect(
    useCallback(() => {
      if (!user) setCommentsFor(null);
      void load();
      return () => {
        sequence.current++;
      };
    }, [load, user]),
  );
  usePetChanges(load);

  const loadMore = async () => {
    if (!nextCursor || loadingMore || refreshing || loading || loadedKey !== currentKey) return;
    const current = sequence.current;
    setLoadingMore(true);
    setPageError('');
    try {
      const data = await fetchFeed(undefined, { limit: 20, scope, cursor: nextCursor });
      if (current !== sequence.current) return;
      setPosts((old) => [
        ...old,
        ...data.posts.filter((item) => !old.some((previous) => previous.id === item.id)),
      ]);
      setNextCursor(data.page.nextCursor);
    } catch (exc) {
      if (current === sequence.current)
        setPageError(exc instanceof Error ? exc.message : '加载更多失败');
    } finally {
      setLoadingMore(false);
    }
  };

  const handleLike = async (post: Post) => {
    if (!user) {
      router.push('/login');
      return;
    }
    const current = sequence.current;
    const key = `like:${currentKey}:${post.id}`;
    if (pending.current.has(key)) return;
    pending.current.add(key);
    try {
      const result = await setLike(post.id, !post.liked_by_me);
      if (current !== sequence.current) return;
      setPosts((current) =>
        current.map((item) =>
          item.id === post.id ? { ...item, liked_by_me: result.liked, likes: result.likes } : item,
        ),
      );
    } catch (exc) {
      Alert.alert('点赞失败', exc instanceof Error ? exc.message : '请重试');
    } finally {
      pending.current.delete(key);
    }
  };

  const handleBookmark = async (post: Post) => {
    if (!user) {
      router.push('/login');
      return;
    }
    const current = sequence.current;
    const key = `bookmark:${currentKey}:${post.id}`;
    if (pending.current.has(key)) return;
    pending.current.add(key);
    try {
      const result = await setBookmark(post.id, !post.bookmarked_by_me);
      if (current !== sequence.current) return;
      setPosts((current) =>
        current.map((item) =>
          item.id === post.id ? { ...item, bookmarked_by_me: result.bookmarked } : item,
        ),
      );
    } catch (exc) {
      Alert.alert('收藏失败', exc instanceof Error ? exc.message : '请重试');
    } finally {
      pending.current.delete(key);
    }
  };

  const handleDelete = async (post: Post) => {
    const current = sequence.current;
    try {
      await deletePost(post.id);
      if (current !== sequence.current) return;
      setPosts((current) => current.filter((item) => item.id !== post.id));
    } catch (exc) {
      Alert.alert('删除失败', exc instanceof Error ? exc.message : '请重试');
    }
  };

  const selectScope = (next: Scope) => {
    if (next === 'following' && !user) {
      router.push('/login');
      return;
    }
    if (next === scope) return;
    setScope(next);
    setLoading(true);
    setPosts([]);
    setNextCursor(null);
  };

  const header = (
    <View style={styles.headerContent}>
      <View style={styles.headerRow}>
        <ThemedText style={[styles.title, { fontFamily: Fonts.rounded }]}>社区</ThemedText>
        <View style={styles.headerActions}>
          <HeaderButton
            label="搜索社区"
            name={{ ios: 'magnifyingglass', android: 'search', web: 'search' }}
            onPress={() => router.push('/community-search')}
          />
          <HeaderButton
            label="附近的人"
            name={{ ios: 'location', android: 'location_on', web: 'location_on' }}
            onPress={() => router.push('/nearby')}
          />
          <HeaderButton
            label={loadedKey === currentKey && unread ? `互动通知，${unread} 条未读` : '互动通知'}
            name={{ ios: 'bell', android: 'notifications_none', web: 'notifications_none' }}
            onPress={() => router.push('/social-inbox')}
            badge={loadedKey === currentKey ? unread : 0}
          />
        </View>
      </View>
      <View style={styles.scopeRow}>
        {(['discover', 'following'] as const).map((item) => (
          <Pressable
            key={item}
            onPress={() => selectScope(item)}
            style={[
              styles.scopeButton,
              { backgroundColor: item === scope ? theme.tint : theme.backgroundSelected },
            ]}
          >
            <ThemedText
              type="smallBold"
              style={{ color: item === scope ? '#ffffff' : theme.textSecondary }}
            >
              {item === 'discover' ? '发现' : '关注'}
            </ThemedText>
          </Pressable>
        ))}
      </View>
      {!!error && visiblePosts.length > 0 && (
        <Pressable
          onPress={() => void load()}
          style={[styles.banner, { backgroundColor: theme.backgroundSelected }]}
          accessibilityLabel="刷新失败，点击重试"
        >
          <ThemedText type="small" style={{ color: theme.danger }}>
            刷新失败：{error}
          </ThemedText>
          <ThemedText type="smallBold" style={{ color: theme.tint }}>
            重试
          </ThemedText>
        </Pressable>
      )}
      <View style={styles.storyHeader}>
        <ThemedText type="smallBold">故事</ThemedText>
        {!!user && (
          <Pressable
            onPress={() => router.push('/story-create')}
            style={styles.storyCreate}
            accessibilityLabel="发布故事"
          >
            <SymbolView
              name={{ ios: 'plus.circle', android: 'add_circle', web: 'add_circle' }}
              size={18}
              tintColor={theme.tint}
            />
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              发布故事
            </ThemedText>
          </Pressable>
        )}
      </View>
      {visibleStories.length ? (
        <FlatList
          horizontal
          data={visibleStories}
          keyExtractor={(item) => String(item.user_id)}
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.storyTray}
          renderItem={({ item }) => (
            <Pressable
              onPress={() => router.push(`/story?userId=${item.user_id}`)}
              style={styles.storyItem}
              accessibilityLabel={`查看 ${item.display_name} 的故事`}
            >
              <View
                style={[
                  styles.storyRing,
                  { borderColor: item.unseen ? theme.tint : theme.backgroundSelected },
                ]}
              >
                <ThemedText style={styles.storyEmoji}>{item.avatar_emoji || '🙂'}</ThemedText>
              </View>
              <ThemedText type="small" numberOfLines={1}>
                {item.display_name}
              </ThemedText>
            </Pressable>
          )}
        />
      ) : storyError ? (
        <ThemedText type="small" style={{ color: theme.danger }}>
          {storyError}
        </ThemedText>
      ) : (
        <ThemedText type="small" themeColor="textSecondary">
          暂无有效故事
        </ThemedText>
      )}
    </View>
  );

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <FlatList
        data={visiblePosts}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={styles.content}
        ListHeaderComponent={header}
        renderItem={({ item }) => (
          <PostCard
            post={item}
            onLike={handleLike}
            onBookmark={handleBookmark}
            onOpenComments={setCommentsFor}
            onOpenPost={(post) => router.push(`/post?id=${post.id}`)}
            onEdit={(post) => router.push(`/post-edit?id=${post.id}`)}
            onOpenAuthor={(post) => router.push(`/user?id=${post.user_id}`)}
            onDelete={handleDelete}
          />
        )}
        ListEmptyComponent={
          loading || loadedKey !== currentKey ? (
            <ActivityIndicator color={theme.tint} />
          ) : (
            <Pressable onPress={() => void load()} style={styles.center}>
              <ThemedText
                type="small"
                style={{ color: error ? theme.danger : theme.textSecondary }}
              >
                {error ||
                  (scope === 'following'
                    ? '还没有关注动态，去发现宠友吧'
                    : '还没有帖子，来分享第一条动态')}
              </ThemedText>
              {!!error && (
                <ThemedText type="small" style={{ color: theme.tint }}>
                  点击重试
                </ThemedText>
              )}
            </Pressable>
          )
        }
        ListFooterComponent={
          pageError ? (
            <Pressable onPress={() => void loadMore()} style={styles.center}>
              <ThemedText type="small" style={{ color: theme.danger }}>
                {pageError} · 点击重试
              </ThemedText>
            </Pressable>
          ) : loadingMore ? (
            <ActivityIndicator color={theme.tint} />
          ) : null
        }
        onEndReached={() => void loadMore()}
        onEndReachedThreshold={0.5}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              void load();
            }}
          />
        }
      />
      <CommentSheet
        key={`${commentsFor?.id ?? 'none'}:${user?.id ?? 0}`}
        post={loadedKey === currentKey ? commentsFor : null}
        onClose={() => setCommentsFor(null)}
        onCommented={(postId, delta = 1) =>
          setPosts((current) =>
            current.map((item) =>
              item.id === postId
                ? { ...item, comments_count: Math.max(0, item.comments_count + delta) }
                : item,
            ),
          )
        }
      />
    </ThemedView>
  );
}

function HeaderButton({
  label,
  name,
  onPress,
  badge = 0,
}: {
  label: string;
  name: Parameters<typeof SymbolView>[0]['name'];
  onPress: () => void;
  badge?: number;
}) {
  const theme = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityLabel={label} style={styles.iconButton}>
      <SymbolView name={name} size={21} tintColor={theme.tint} />
      {badge > 0 && (
        <View style={[styles.badge, { backgroundColor: theme.danger }]}>
          <ThemedText style={[styles.badgeText, { color: '#ffffff' }]}>
            {badge > 99 ? '99+' : badge}
          </ThemedText>
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: {
    paddingHorizontal: Spacing.four,
    paddingBottom: BottomTabInset + Spacing.five,
    gap: Spacing.four,
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
  },
  headerContent: { gap: Spacing.three, marginBottom: Spacing.three },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: 30, fontWeight: '700', lineHeight: 38 },
  headerActions: { flexDirection: 'row', gap: Spacing.one },
  iconButton: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  badge: {
    position: 'absolute',
    top: 4,
    right: 2,
    minWidth: 16,
    height: 16,
    borderRadius: 8,
    paddingHorizontal: 4,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badgeText: { fontSize: 10, lineHeight: 12, fontWeight: '700' },
  banner: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: Spacing.two,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two,
    borderRadius: 12,
  },
  scopeRow: { flexDirection: 'row', gap: Spacing.two },
  scopeButton: { paddingHorizontal: Spacing.four, paddingVertical: Spacing.two, borderRadius: 18 },
  storyHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  storyCreate: { flexDirection: 'row', alignItems: 'center', gap: Spacing.one },
  storyTray: { gap: Spacing.three },
  storyItem: { alignItems: 'center', width: 68, gap: Spacing.one },
  storyRing: {
    borderWidth: 2,
    width: 58,
    height: 58,
    borderRadius: 29,
    alignItems: 'center',
    justifyContent: 'center',
  },
  storyEmoji: { fontSize: 24 },
  center: { alignItems: 'center', paddingVertical: Spacing.five, gap: Spacing.two },
});
