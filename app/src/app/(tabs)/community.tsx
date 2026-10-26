import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CommentSheet } from '@/components/social/comment-sheet';
import { PostCard } from '@/components/social/post-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { usePetChanges } from '@/hooks/use-pet-changes';
import { useTheme } from '@/hooks/use-theme';
import { deletePost, fetchFeed, toggleLike } from '@/services/api';
import type { Post } from '@/types/social';

type Story = { key: string; userId: number | null; emoji: string; name: string };

/**
 * 社区页（ins 风格）：渐变感故事环 + 可搜索的帖子流。
 * 互动闭环：点赞 / 评论 / 分享 / 删除自己的帖子 / 举报他人帖子。
 * 浏览无需登录，互动需登录。
 */
export default function CommunityScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [posts, setPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [commentsFor, setCommentsFor] = useState<Post | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');

  const sequence = useRef(0);
  const load = useCallback(() => {
    const current = ++sequence.current;
    return fetchFeed()
      .then((data) => {
        if (current !== sequence.current) return;
        setPosts(data.posts);
        setError(null);
        setLoading(false);
        setRefreshing(false);
      })
      .catch(() => {
        if (current !== sequence.current) return;
        setError('社区动态加载失败，下拉重试');
        setLoading(false);
        setRefreshing(false);
      });
  }, []);

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

  const handleLike = (post: Post) => {
    if (!user) {
      router.push('/login');
      return;
    }
    setPosts((current) =>
      current.map((item) =>
        item.id === post.id
          ? {
              ...item,
              liked_by_me: !item.liked_by_me,
              likes: item.likes + (item.liked_by_me ? -1 : 1),
            }
          : item,
      ),
    );
    toggleLike(post.id)
      .then((result) => {
        setPosts((current) =>
          current.map((item) =>
            item.id === post.id
              ? { ...item, liked_by_me: result.liked, likes: result.likes }
              : item,
          ),
        );
      })
      .catch(() => void load());
  };

  const handleDelete = (post: Post) => {
    deletePost(post.id)
      .then(() => setPosts((current) => current.filter((item) => item.id !== post.id)))
      .catch(() => void load());
  };

  const keyword = query.trim().toLowerCase();
  const visiblePosts = keyword
    ? posts.filter((post) =>
        `${post.caption} ${post.display_name} ${post.pet_name ?? ''} ${post.location_text}`
          .toLowerCase()
          .includes(keyword),
      )
    : posts;

  const stories: Story[] = [];
  if (user) stories.push({ key: 'me', userId: null, emoji: user.avatarEmoji, name: '我的故事' });
  for (const post of posts) {
    if (stories.some((item) => item.userId === post.user_id)) continue;
    if (user && post.user_id === user.id) continue;
    stories.push({
      key: `u${post.user_id}`,
      userId: post.user_id,
      emoji: post.avatar_emoji,
      name: post.display_name,
    });
  }

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => {
            setRefreshing(true);
            void load();
          }}
        />
      }
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.two }]}>
        {/* 标题 + 搜索圆钮 */}
        <View style={styles.header}>
          <ThemedText style={[styles.title, { fontFamily: Fonts.rounded }]}>社区</ThemedText>
          <Pressable
            onPress={() => {
              setSearchOpen((value) => !value);
              setQuery('');
            }}
            style={[
              styles.searchButton,
              {
                backgroundColor: theme.backgroundElement,
                boxShadow: Shadows.chip,
              },
            ]}
            accessibilityLabel="搜索社区"
          >
            <SymbolView
              name={{ ios: 'magnifyingglass', android: 'search', web: 'search' }}
              size={19}
              tintColor={theme.tint}
            />
          </Pressable>
        </View>

        {searchOpen && (
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="搜索帖子、宠友、地点…"
            placeholderTextColor={theme.textSecondary}
            autoFocus
            style={[
              styles.searchInput,
              { color: theme.text, borderColor: theme.backgroundSelected },
            ]}
          />
        )}

        {/* 故事环：珊瑚描边 + 光晕感 */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.stories}
        >
          {stories.map((story) => (
            <Pressable
              key={story.key}
              style={styles.story}
              onPress={() =>
                story.userId ? router.push(`/user?id=${story.userId}`) : router.push('/create')
              }
            >
              <View
                style={[
                  styles.storyRing,
                  { borderColor: theme.tint, boxShadow: `0 0 0 3px ${theme.tint}1A` },
                ]}
              >
                <View style={[styles.storyAvatar, { backgroundColor: theme.tintBackground }]}>
                  <ThemedText style={{ fontSize: 24 }}>{story.emoji || '🙂'}</ThemedText>
                </View>
              </View>
              <ThemedText
                type="small"
                themeColor="textSecondary"
                style={styles.storyName}
                numberOfLines={1}
              >
                {story.name}
              </ThemedText>
            </Pressable>
          ))}
        </ScrollView>

        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator color={theme.tint} />
          </View>
        ) : error ? (
          <ThemedText type="small" themeColor="textSecondary" style={styles.centerText}>
            {error}
          </ThemedText>
        ) : visiblePosts.length === 0 ? (
          <View style={styles.center}>
            <ThemedText style={styles.emptyEmoji}>{query ? '🔍' : '🐕'}</ThemedText>
            <ThemedText type="small" themeColor="textSecondary" style={styles.centerText}>
              {query
                ? `没有找到与「${query}」相关的内容`
                : '还没有帖子，点右下角 ＋ 分享第一张宠物照片'}
            </ThemedText>
          </View>
        ) : (
          <View style={styles.feed}>
            {visiblePosts.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                onLike={handleLike}
                onOpenComments={setCommentsFor}
                onOpenAuthor={(item) => router.push(`/user?id=${item.user_id}`)}
                onDelete={handleDelete}
              />
            ))}
          </View>
        )}
      </ThemedView>

      <CommentSheet
        key={commentsFor?.id ?? 'none'}
        post={commentsFor}
        onClose={() => setCommentsFor(null)}
        onCommented={(postId) => {
          setPosts((current) =>
            current.map((item) =>
              item.id === postId ? { ...item, comments_count: item.comments_count + 1 } : item,
            ),
          );
        }}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scrollView: { flex: 1 },
  contentContainer: { flexDirection: 'row', justifyContent: 'center' },
  container: {
    flexGrow: 1,
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    paddingBottom: BottomTabInset + Spacing.five,
    gap: Spacing.three,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  title: { fontSize: 30, fontWeight: '700', lineHeight: 38 },
  searchButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  searchInput: {
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 10,
    fontSize: 14,
  },
  stories: { gap: Spacing.three },
  story: { alignItems: 'center', gap: 6, width: 70 },
  storyRing: {
    width: 64,
    height: 64,
    borderRadius: 32,
    borderWidth: 2.5,
    alignItems: 'center',
    justifyContent: 'center',
  },
  storyAvatar: {
    width: 52,
    height: 52,
    borderRadius: 26,
    alignItems: 'center',
    justifyContent: 'center',
  },
  storyName: { fontSize: 11, lineHeight: 14 },
  center: { alignItems: 'center', paddingVertical: Spacing.six, gap: Spacing.two },
  centerText: { textAlign: 'center', paddingVertical: Spacing.four },
  emptyEmoji: { fontSize: 38 },
  feed: { gap: Spacing.four },
});
