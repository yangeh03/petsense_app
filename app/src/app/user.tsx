import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CommentSheet } from '@/components/social/comment-sheet';
import { FollowListModal } from '@/components/social/follow-list-modal';
import { PostCard } from '@/components/social/post-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { usePetChanges } from '@/hooks/use-pet-changes';
import { useTheme } from '@/hooks/use-theme';
import {
  absoluteUrl,
  deletePost,
  fetchFeed,
  fetchUserProfile,
  setFollow,
  setLike,
  setBookmark,
} from '@/services/api';
import type { Post, UserProfile } from '@/types/social';

/** 用户主页：资料卡 + 关注按钮 + 粉丝/关注列表 + TA 的帖子（评论/删除闭环）。 */
export default function UserScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const params = useLocalSearchParams<{ id: string }>();
  const userId = Number(params.id);
  const { user } = useAuth();
  const [storedProfile, setProfile] = useState<UserProfile | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const viewerId = user?.id ?? 0;
  const profile = loadedFor === viewerId ? storedProfile : null;
  const visiblePosts = loadedFor === viewerId ? posts : [];
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [commentsFor, setCommentsFor] = useState<Post | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pageError, setPageError] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);
  const [followPending, setFollowPending] = useState(false);
  const [followListKind, setFollowListKind] = useState<'followers' | 'following' | null>(null);
  const isMe = user?.id === userId;

  const sequence = useRef(0);
  const load = useCallback(async () => {
    if (!Number.isFinite(userId)) {
      setLoadError('用户不存在或已注销');
      setLoading(false);
      return;
    }
    const current = ++sequence.current;
    try {
      const [profileData, feedData] = await Promise.all([
        fetchUserProfile(userId),
        fetchFeed(userId, { limit: 20 }),
      ]);
      if (current !== sequence.current) return;
      setProfile(profileData.profile);
      setPosts(feedData.posts);
      setLoadedFor(viewerId);
      setNextCursor(feedData.page.nextCursor);
      setPageError('');
      setLoadError('');
    } catch {
      if (current === sequence.current) {
        if (loadedFor !== viewerId) {
          setProfile(null);
          setPosts([]);
        }
        setLoadedFor(viewerId);
        setLoadError('资料加载失败，请检查网络后重试');
      }
    } finally {
      if (current === sequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [userId, viewerId, loadedFor]);

  useFocusEffect(
    useCallback(() => {
      if (!user) setFollowListKind(null);
      void load();
      return () => {
        sequence.current++;
      };
    }, [load, user]),
  );

  usePetChanges(load);

  const requireLogin = () => {
    if (!user) {
      router.push('/login');
      return true;
    }
    return false;
  };

  const handleFollow = () => {
    if (requireLogin() || !profile || followPending) return;
    const currentRequest = sequence.current;
    setFollowPending(true);
    void setFollow(userId, !profile.is_following)
      .then((data) => {
        if (currentRequest !== sequence.current) return;
        setProfile((current) =>
          current
            ? {
                ...current,
                is_following: data.following,
                followers:
                  current.followers +
                  (data.following === current.is_following ? 0 : data.following ? 1 : -1),
              }
            : current,
        );
      })
      .catch((exc) => Alert.alert('关注失败', exc instanceof Error ? exc.message : '请重试'))
      .finally(() => setFollowPending(false));
  };

  const handleDelete = (post: Post) => {
    const currentRequest = sequence.current;
    void deletePost(post.id)
      .then(() => {
        if (currentRequest !== sequence.current) return;
        setPosts((current) => current.filter((item) => item.id !== post.id));
        setProfile((current) =>
          current ? { ...current, posts_count: current.posts_count - 1 } : current,
        );
      })
      .catch((exc) => Alert.alert('删除失败', exc instanceof Error ? exc.message : '请重试'));
  };

  const loadMore = async () => {
    if (!nextCursor || loadingMore || loadedFor !== viewerId) return;
    const currentRequest = sequence.current;
    setLoadingMore(true);
    try {
      const data = await fetchFeed(userId, { cursor: nextCursor, limit: 20 });
      if (currentRequest !== sequence.current) return;
      setPosts((current) => [
        ...current,
        ...data.posts.filter((item) => !current.some((existing) => existing.id === item.id)),
      ]);
      setNextCursor(data.page.nextCursor);
      setPageError('');
    } catch (exc) {
      if (currentRequest === sequence.current)
        setPageError(exc instanceof Error ? exc.message : '加载更多失败');
    } finally {
      setLoadingMore(false);
    }
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
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.three }]}>
        <Pressable
          onPress={() => router.back()}
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel="返回"
          style={styles.back}
        >
          <SymbolView
            name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
            size={20}
            tintColor={theme.tint}
          />
          <ThemedText type="smallBold" style={{ color: theme.tint }}>
            返回
          </ThemedText>
        </Pressable>

        {loading || loadedFor !== viewerId ? (
          <View style={styles.center}>
            <ActivityIndicator color={theme.tint} />
          </View>
        ) : loadError && !profile ? (
          <View style={styles.center}>
            <ThemedText style={styles.errorEmoji}>🐾</ThemedText>
            <ThemedText type="small" themeColor="textSecondary" style={styles.centerText}>
              {loadError}
            </ThemedText>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="重试加载"
              onPress={() => {
                setLoading(true);
                void load();
              }}
              style={({ pressed }) => [
                styles.retryButton,
                { backgroundColor: theme.tint },
                pressed && { opacity: 0.85 },
              ]}
            >
              <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                重试
              </ThemedText>
            </Pressable>
          </View>
        ) : !profile ? (
          <View style={styles.center}>
            <ActivityIndicator color={theme.tint} />
          </View>
        ) : (
          <>
            <View style={styles.headerCard}>
              <View style={[styles.avatar, { backgroundColor: theme.tintBackground }]}>
                <ThemedText style={{ fontSize: 34 }}>{profile.avatarEmoji}</ThemedText>
              </View>
              <View style={styles.headerTexts}>
                <ThemedText type="smallBold" style={styles.name}>
                  {profile.displayName}
                </ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  @{profile.username} · {profile.pets.length} 只宠物
                </ThemedText>
              </View>
              {!isMe && (
                <Pressable
                  onPress={handleFollow}
                  disabled={followPending}
                  style={({ pressed }) => [
                    styles.followButton,
                    {
                      backgroundColor: profile.is_following ? theme.backgroundSelected : theme.tint,
                    },
                    pressed && { opacity: 0.85 },
                  ]}
                >
                  <ThemedText
                    type="smallBold"
                    style={{ color: profile.is_following ? theme.textSecondary : '#ffffff' }}
                  >
                    {profile.is_following ? '已关注' : '关注'}
                  </ThemedText>
                </Pressable>
              )}
            </View>

            <ThemedView type="backgroundElement" style={styles.statsRow}>
              <StatCell value={profile.posts_count} label="帖子" />
              <StatCell
                value={profile.followers}
                label="粉丝"
                onPress={() => setFollowListKind('followers')}
              />
              <StatCell
                value={profile.following}
                label="关注"
                onPress={() => setFollowListKind('following')}
              />
            </ThemedView>

            {profile.pets.length > 0 && (
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.petRow}
              >
                {profile.pets.map((pet) => (
                  <View
                    key={pet.id}
                    style={[styles.petChip, { backgroundColor: theme.tintBackground }]}
                  >
                    {pet.avatarImageUrl ? (
                      <Image
                        source={{ uri: absoluteUrl(pet.avatarImageUrl) }}
                        style={{ width: 32, height: 32, borderRadius: 16 }}
                        contentFit="cover"
                      />
                    ) : (
                      <ThemedText style={{ fontSize: 22 }}>{pet.avatar_emoji}</ThemedText>
                    )}
                    <ThemedText type="small">{pet.name}</ThemedText>
                  </View>
                ))}
              </ScrollView>
            )}

            <View style={styles.feed}>
              {visiblePosts.length === 0 ? (
                <ThemedText type="small" themeColor="textSecondary" style={styles.empty}>
                  TA 还没有发过帖子
                </ThemedText>
              ) : (
                visiblePosts.map((post) => (
                  <PostCard
                    key={post.id}
                    post={post}
                    onLike={() => {
                      if (requireLogin()) return;
                      const currentRequest = sequence.current;
                      setLike(post.id, !post.liked_by_me)
                        .then((result) => {
                          if (currentRequest !== sequence.current) return;
                          setPosts((current) =>
                            current.map((item) =>
                              item.id === post.id
                                ? { ...item, liked_by_me: result.liked, likes: result.likes }
                                : item,
                            ),
                          );
                        })
                        .catch(() => { if (currentRequest === sequence.current) void load(); });
                    }}
                    onOpenComments={setCommentsFor}
                    onOpenPost={(item) => router.push(`/post?id=${item.id}`)}
                    onEdit={(item) => router.push(`/post-edit?id=${item.id}`)}
                    onBookmark={(item) => {
                      if (requireLogin()) return;
                      const currentRequest = sequence.current;
                      void setBookmark(item.id, !item.bookmarked_by_me)
                        .then((data) => {
                          if (currentRequest !== sequence.current) return;
                          setPosts((current) =>
                            current.map((row) =>
                              row.id === item.id
                                ? { ...row, bookmarked_by_me: data.bookmarked }
                                : row,
                            ),
                          );
                        })
                        .catch((exc) =>
                          Alert.alert('收藏失败', exc instanceof Error ? exc.message : '请重试'),
                        );
                    }}
                    onOpenAuthor={() => {}}
                    onDelete={handleDelete}
                  />
                ))
              )}
              {!!nextCursor && (
                <Pressable onPress={() => void loadMore()}>
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    {loadingMore ? '加载中…' : '加载更多帖子'}
                  </ThemedText>
                </Pressable>
              )}
              {!!pageError && (
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {pageError}
                </ThemedText>
              )}
            </View>
          </>
        )}
      </ThemedView>

      <CommentSheet
        key={`${commentsFor?.id ?? 'none'}:${viewerId}`}
        post={loadedFor === viewerId ? commentsFor : null}
        onClose={() => setCommentsFor(null)}
        onCommented={(postId, delta = 1) => {
          setPosts((current) =>
            current.map((item) =>
              item.id === postId
                ? { ...item, comments_count: Math.max(0, item.comments_count + delta) }
                : item,
            ),
          );
        }}
      />

      <FollowListModal
        visible={followListKind !== null}
        userId={userId}
        kind={followListKind ?? 'followers'}
        onClose={() => setFollowListKind(null)}
      />
    </ScrollView>
  );
}

function StatCell({
  value,
  label,
  onPress,
}: {
  value: number;
  label: string;
  onPress?: () => void;
}) {
  const content = (
    <>
      <ThemedText style={styles.statValue}>{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
    </>
  );
  if (onPress) {
    return (
      <Pressable onPress={onPress} style={styles.statCell}>
        {content}
      </Pressable>
    );
  }
  return <View style={styles.statCell}>{content}</View>;
}

const styles = StyleSheet.create({
  scrollView: { flex: 1 },
  contentContainer: { flexDirection: 'row', justifyContent: 'center' },
  container: {
    flexGrow: 1,
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.six,
    gap: Spacing.three,
  },
  back: {
    alignSelf: 'flex-start',
    marginBottom: -Spacing.two,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  center: { alignItems: 'center', paddingVertical: Spacing.six },
  errorEmoji: { fontSize: 38 },
  centerText: { textAlign: 'center', paddingVertical: Spacing.two },
  retryButton: {
    borderRadius: 14,
    paddingHorizontal: Spacing.five,
    paddingVertical: 8,
    marginTop: Spacing.two,
  },
  headerCard: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  avatar: {
    width: 64,
    height: 64,
    borderRadius: 32,
    alignItems: 'center',
    justifyContent: 'center',
  },
  headerTexts: { gap: 2, flexShrink: 1 },
  name: { fontSize: 18 },
  followButton: { borderRadius: 14, paddingHorizontal: Spacing.four, paddingVertical: 8 },
  statsRow: { flexDirection: 'row', borderRadius: 20, paddingVertical: Spacing.three },
  statCell: { flex: 1, alignItems: 'center', gap: 2 },
  statValue: { fontSize: 20, fontWeight: '700', lineHeight: 24 },
  petRow: { gap: Spacing.two },
  petChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderRadius: 16,
    paddingHorizontal: Spacing.three,
    paddingVertical: 8,
  },
  feed: { gap: Spacing.four },
  empty: { textAlign: 'center', paddingVertical: Spacing.five },
});
