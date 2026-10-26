import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CommentSheet } from '@/components/social/comment-sheet';
import { PostCard } from '@/components/social/post-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import {
  ApiError,
  deletePost,
  fetchPost,
  isProtectedSocialMedia,
  setBookmark,
  setLike,
  socialImageSource,
} from '@/services/api';
import type { Post } from '@/types/social';

export default function PostScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const params = useLocalSearchParams<{ id: string }>();
  const id = Number(params.id);
  const [loadedPost, setPost] = useState<Post | null>(null);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const viewerId = user?.id ?? 0;
  const post = loadedFor === viewerId ? loadedPost : null;
  const sequence = useRef(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [fullImage, setFullImage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const current = ++sequence.current;
    if (!Number.isSafeInteger(id) || id <= 0) {
      setError('帖子链接无效');
      setLoadedFor(viewerId);
      setLoading(false);
      return;
    }
    try {
      const data = await fetchPost(id);
      if (current !== sequence.current) return;
      setPost(data.post);
      setLoadedFor(viewerId);
      setError('');
    } catch (exc) {
      if (current !== sequence.current) return;
      if (exc instanceof ApiError && exc.status === 404) setPost(null);
      setLoadedFor(viewerId);
      setError(exc instanceof Error ? exc.message : '帖子加载失败，请重试');
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [id, viewerId]);

  useFocusEffect(
    useCallback(() => {
      void load();
      return () => {
        sequence.current++;
      };
    }, [load]),
  );

  const like = async (item: Post) => {
    if (!user) {
      router.push('/login');
      return;
    }
    try {
      const result = await setLike(item.id, !item.liked_by_me);
      setPost((current) =>
        current ? { ...current, liked_by_me: result.liked, likes: result.likes } : current,
      );
    } catch (exc) {
      Alert.alert('点赞失败', exc instanceof Error ? exc.message : '请重试');
    }
  };

  const bookmark = async (item: Post) => {
    if (!user) {
      router.push('/login');
      return;
    }
    try {
      const result = await setBookmark(item.id, !item.bookmarked_by_me);
      setPost((current) =>
        current ? { ...current, bookmarked_by_me: result.bookmarked } : current,
      );
    } catch (exc) {
      Alert.alert('收藏失败', exc instanceof Error ? exc.message : '请重试');
    }
  };

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} accessibilityLabel="返回社区">
          <SymbolView
            name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
            size={24}
            tintColor={theme.tint}
          />
        </Pressable>
        <ThemedText type="subtitle">帖子详情</ThemedText>
        <View style={styles.headerSpacer} />
      </View>
      {loading || loadedFor !== viewerId ? (
        <ActivityIndicator color={theme.tint} />
      ) : error && !post ? (
        <Pressable
          onPress={() => {
            setLoading(true);
            void load();
          }}
          style={styles.center}
        >
          <ThemedText style={{ color: theme.danger }}>{error}</ThemedText>
          <ThemedText type="small" style={{ color: theme.tint }}>
            点击重试
          </ThemedText>
        </Pressable>
      ) : post ? (
        <ScrollView contentContainerStyle={styles.content}>
          <PostCard
            post={post}
            expanded
            onLike={like}
            onBookmark={bookmark}
            onOpenPost={(item) => {
              const media = item.media?.[0]?.url ?? item.imageUrl;
              if (media) setFullImage(media);
            }}
            onOpenComments={() => setCommentsOpen(true)}
            onEdit={(item) => router.push(`/post-edit?id=${item.id}`)}
            onOpenAuthor={(item) => router.push(`/user?id=${item.user_id}`)}
            onDelete={(item) => {
              void deletePost(item.id)
                .then(() => router.back())
                .catch((exc) =>
                  Alert.alert('删除失败', exc instanceof Error ? exc.message : '请重试'),
                );
            }}
          />
          {(post.media ?? []).length > 1 && (
            <ScrollView horizontal contentContainerStyle={styles.gallery}>
              {post.media?.map((media) => (
                <Pressable key={media.url} onPress={() => setFullImage(media.url)}>
                  <Image
                    source={socialImageSource(media.url)}
                    cachePolicy={isProtectedSocialMedia(media.url) ? 'none' : 'memory-disk'}
                    style={styles.thumbnail}
                    contentFit="cover"
                  />
                </Pressable>
              ))}
            </ScrollView>
          )}
          <ThemedText type="small" themeColor="textSecondary">
            {new Date(post.created_at).toLocaleString()}
          </ThemedText>
        </ScrollView>
      ) : null}
      <CommentSheet
        key={`${commentsOpen ? id : 'closed'}:${viewerId}`}
        post={commentsOpen ? post : null}
        onClose={() => setCommentsOpen(false)}
        onCommented={(postId, delta = 1) =>
          setPost((current) =>
            current?.id === postId
              ? { ...current, comments_count: Math.max(0, current.comments_count + delta) }
              : current,
          )
        }
      />
      <Modal visible={!!fullImage} onRequestClose={() => setFullImage(null)}>
        <ThemedView style={[styles.fullscreen, { backgroundColor: theme.background }]}>
          <Pressable
            onPress={() => setFullImage(null)}
            style={styles.closeImage}
            accessibilityLabel="关闭图片"
          >
            <SymbolView
              name={{ ios: 'xmark', android: 'close', web: 'close' }}
              size={26}
              tintColor={theme.text}
            />
          </Pressable>
          {!!fullImage && (
            <Image
              source={socialImageSource(fullImage)}
              style={styles.fullImage}
              cachePolicy={isProtectedSocialMedia(fullImage) ? 'none' : 'memory-disk'}
              contentFit="contain"
            />
          )}
        </ThemedView>
      </Modal>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.two,
  },
  headerSpacer: { width: 24 },
  center: { alignItems: 'center', padding: Spacing.four, gap: Spacing.two },
  content: {
    padding: Spacing.four,
    gap: Spacing.three,
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
  },
  gallery: { gap: Spacing.two },
  thumbnail: { width: 96, height: 96, borderRadius: 12 },
  fullscreen: { flex: 1, justifyContent: 'center' },
  closeImage: { position: 'absolute', top: 55, right: Spacing.four, zIndex: 1 },
  fullImage: { width: '100%', height: '85%' },
});
