import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CommentSheet } from '@/components/social/comment-sheet';
import { PostCard } from '@/components/social/post-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchFeed, toggleLike } from '@/services/api';
import type { Post } from '@/types/social';

type Story = { key: string; userId: number | null; emoji: string; name: string };

/** 社区页：故事环 + 宠物帖子信息流（浏览无需登录，发帖点赞在登录后可用）。 */
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

  const load = useCallback(async () => {
    try {
      const data = await fetchFeed();
      setPosts(data.posts);
      setError(null);
    } catch {
      setError('社区动态加载失败，下拉重试');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

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
      .catch(() => {
        void load();
      });
  };

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
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.four }]}>
        <ThemedText type="subtitle">社区</ThemedText>

        {/* 故事环 */}
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
              <View style={[styles.storyRing, { borderColor: theme.tint }]}>
                <View style={[styles.storyAvatar, { backgroundColor: theme.tintBackground }]}>
                  <ThemedText style={{ fontSize: 22 }}>{story.emoji || '🙂'}</ThemedText>
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
        ) : posts.length === 0 ? (
          <View style={styles.center}>
            <ThemedText style={styles.emptyEmoji}>🐕</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              还没有帖子，点右下角 ＋ 分享第一张宠物照片
            </ThemedText>
          </View>
        ) : (
          <View style={styles.feed}>
            {posts.map((post) => (
              <PostCard
                key={post.id}
                post={post}
                onLike={handleLike}
                onOpenComments={setCommentsFor}
                onOpenAuthor={(item) => router.push(`/user?id=${item.user_id}`)}
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
  stories: { gap: Spacing.three },
  story: { alignItems: 'center', gap: 6, width: 68 },
  storyRing: {
    width: 62,
    height: 62,
    borderRadius: 31,
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
