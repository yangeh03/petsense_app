import { AppIcon } from '@/components/ui/icon';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
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

import { PostCard } from '@/components/social/post-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchFeed, fetchUserProfile, toggleFollow, toggleLike } from '@/services/api';
import type { Post, UserProfile } from '@/types/social';

/** 用户主页：资料卡 + 关注按钮 + TA 的帖子。 */
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
  const [refreshing, setRefreshing] = useState(false);
  const isMe = user?.id === userId;

  const load = useCallback(async () => {
    if (!Number.isFinite(userId)) return;
    try {
      const [profileData, feedData] = await Promise.all([
        fetchUserProfile(userId),
        fetchFeed(userId),
      ]);
      setProfile(profileData.profile);
      setPosts(feedData.posts);
    } catch {
      // 下拉重试
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [userId]);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  const handleFollow = () => {
    if (!user) {
      router.push('/login');
      return;
    }
    if (!profile) return;
    setProfile({
      ...profile,
      is_following: !profile.is_following,
      followers: profile.followers + (profile.is_following ? -1 : 1),
    });
    toggleFollow(userId).catch(() => void load());
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
        <Pressable onPress={() => router.back()} hitSlop={10} style={styles.back}>
          <AppIcon name="chevron-left" size={20} color={theme.text} />
        </Pressable>

        {loading || !profile ? (
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
              <StatCell value={profile.followers} label="粉丝" />
              <StatCell value={profile.following} label="关注" />
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
                    <ThemedText style={{ fontSize: 22 }}>{pet.avatar_emoji}</ThemedText>
                    <ThemedText type="small" style={styles.petName}>
                      {pet.name}
                    </ThemedText>
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
                      if (!user) {
                        router.push('/login');
                        return;
                      }
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
                    onOpenComments={() => router.push('/community')}
                    onOpenAuthor={() => {
                      // 已在该用户页
                    }}
                  />
                ))
              )}
            </View>
          </>
        )}
      </ThemedView>
    </ScrollView>
  );
}

function StatCell({ value, label }: { value: number; label: string }) {
  return (
    <View style={styles.statCell}>
      <ThemedText style={styles.statValue}>{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
    </View>
  );
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
  back: { alignSelf: 'flex-start', marginBottom: -Spacing.two },
  center: { alignItems: 'center', paddingVertical: Spacing.six },
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
  petName: { color: '#3A2E26' },
  feed: { gap: Spacing.four },
  empty: { textAlign: 'center', paddingVertical: Spacing.five },
});
