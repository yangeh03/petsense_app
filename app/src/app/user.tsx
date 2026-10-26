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
  toggleFollow,
  toggleLike,
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
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [posts, setPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [commentsFor, setCommentsFor] = useState<Post | null>(null);
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
        fetchFeed(userId),
      ]);
      if (current !== sequence.current) return;
      setProfile(profileData.profile);
      setPosts(feedData.posts);
      setLoadError('');
    } catch {
      if (current === sequence.current) setLoadError('资料加载失败，请检查网络后重试');
    } finally {
      if (current === sequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [userId]);

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
    if (requireLogin() || !profile) return;
    setProfile({
      ...profile,
      is_following: !profile.is_following,
      followers: profile.followers + (profile.is_following ? -1 : 1),
    });
    toggleFollow(userId).catch(() => void load());
  };

  const handleDelete = (post: Post) => {
    Alert.alert('删除帖子', '删除后无法恢复，确定删除？', [
      { text: '取消', style: 'cancel' },
      {
        text: '删除',
        style: 'destructive',
        onPress: () => {
          deletePost(post.id)
            .then(() => {
              setPosts((current) => current.filter((item) => item.id !== post.id));
              setProfile((current) =>
                current ? { ...current, posts_count: current.posts_count - 1 } : current,
              );
            })
            .catch(() => void load());
        },
      },
    ]);
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

        {loading ? (
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
              {posts.length === 0 ? (
                <ThemedText type="small" themeColor="textSecondary" style={styles.empty}>
                  TA 还没有发过帖子
                </ThemedText>
              ) : (
                posts.map((post) => (
                  <PostCard
                    key={post.id}
                    post={post}
                    onLike={() => {
                      if (requireLogin()) return;
                      toggleLike(post.id)
                        .then((result) =>
                          setPosts((current) =>
                            current.map((item) =>
                              item.id === post.id
                                ? { ...item, liked_by_me: result.liked, likes: result.likes }
                                : item,
                            ),
                          ),
                        )
                        .catch(() => void load());
                    }}
                    onOpenComments={setCommentsFor}
                    onOpenAuthor={() => {}}
                    onDelete={handleDelete}
                  />
                ))
              )}
            </View>
          </>
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
