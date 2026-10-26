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

type Failure = { kind: 'invalid' | 'unavailable' | 'network'; message: string } | null;

export default function PostScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const params = useLocalSearchParams<{ id: string; commentId?: string }>();
  const id = Number(params.id);
  const focusCommentId = Number(params.commentId) || undefined;
  const [loadedPost, setPost] = useState<Post | null>(null);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const viewerId = user?.id ?? 0;
  const post = loadedFor === viewerId ? loadedPost : null;
  const sequence = useRef(0);
  const [failure, setFailure] = useState<Failure>(null);
  const [loading, setLoading] = useState(true);
  const [commentsOpen, setCommentsOpen] = useState(!!focusCommentId);
  const [fullIndex, setFullIndex] = useState<number | null>(null);
  const gallery = post?.media?.length
    ? post.media.map((media) => media.url)
    : post?.imageUrl
      ? [post.imageUrl]
      : [];
  const fullImage = fullIndex !== null ? gallery[fullIndex] : undefined;
  const stepGallery = (delta: number) =>
    setFullIndex((current) =>
      current === null ? current : Math.min(gallery.length - 1, Math.max(0, current + delta)),
    );

  const load = useCallback(async () => {
    const current = ++sequence.current;
    if (!Number.isSafeInteger(id) || id <= 0) {
      setFailure({ kind: 'invalid', message: '帖子链接无效' });
      setLoadedFor(viewerId);
      setLoading(false);
      return;
    }
    try {
      const data = await fetchPost(id);
      if (current !== sequence.current) return;
      setPost(data.post);
      setLoadedFor(viewerId);
      setFailure(null);
    } catch (exc) {
      if (current !== sequence.current) return;
      setLoadedFor(viewerId);
      // 404/410 是确定的终态：内容已删除、受众不包含当前访客，或从未存在。重试没有意义。
      if (exc instanceof ApiError && (exc.status === 404 || exc.status === 410)) {
        setPost(null);
        setFailure({ kind: 'unavailable', message: '内容已删除或对你不可见' });
      } else {
        setFailure({
          kind: 'network',
          message: exc instanceof Error ? exc.message : '帖子加载失败，请重试',
        });
      }
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
      ) : failure && !post ? (
        failure.kind === 'network' ? (
          <Pressable
            onPress={() => {
              setLoading(true);
              void load();
            }}
            style={styles.center}
            accessibilityLabel="重新加载帖子"
          >
            <ThemedText style={{ color: theme.danger }}>{failure.message}</ThemedText>
            <ThemedText type="small" style={{ color: theme.tint }}>
              点击重试
            </ThemedText>
          </Pressable>
        ) : (
          <View style={styles.center}>
            <SymbolView
              name={{
                ios: failure.kind === 'invalid' ? 'link.badge.plus' : 'eye.slash',
                android: failure.kind === 'invalid' ? 'link_off' : 'visibility_off',
                web: failure.kind === 'invalid' ? 'link_off' : 'visibility_off',
              }}
              size={32}
              tintColor={theme.textSecondary}
            />
            <ThemedText themeColor="textSecondary">{failure.message}</ThemedText>
            <Pressable
              onPress={() => router.back()}
              style={[styles.backButton, { backgroundColor: theme.backgroundSelected }]}
              accessibilityLabel="返回"
            >
              <ThemedText type="smallBold">返回</ThemedText>
            </Pressable>
          </View>
        )
      ) : post ? (
        <ScrollView contentContainerStyle={styles.content}>
          <PostCard
            post={post}
            expanded
            onLike={like}
            onBookmark={bookmark}
            onOpenPost={() => {
              if (gallery.length) setFullIndex(0);
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
              {post.media?.map((media, index) => (
                <Pressable
                  key={media.url}
                  onPress={() => setFullIndex(index)}
                  accessibilityLabel={`查看第 ${index + 1} 张图片`}
                >
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
        focusCommentId={focusCommentId}
        onClose={() => setCommentsOpen(false)}
        onCommented={(postId, delta = 1) =>
          setPost((current) =>
            current?.id === postId
              ? { ...current, comments_count: Math.max(0, current.comments_count + delta) }
              : current,
          )
        }
      />
      <Modal visible={fullIndex !== null} onRequestClose={() => setFullIndex(null)}>
        <ThemedView style={[styles.fullscreen, { backgroundColor: theme.background }]}>
          <Pressable
            onPress={() => setFullIndex(null)}
            style={[styles.closeImage, { top: insets.top + Spacing.two }]}
            accessibilityLabel="关闭图片"
          >
            <SymbolView
              name={{ ios: 'xmark', android: 'close', web: 'close' }}
              size={26}
              tintColor={theme.text}
            />
          </Pressable>
          {gallery.length > 1 && fullIndex !== null && (
            <ThemedText
              type="smallBold"
              style={[styles.pageIndicator, { top: insets.top + Spacing.two }]}
              accessibilityLiveRegion="polite"
            >
              {fullIndex + 1} / {gallery.length}
            </ThemedText>
          )}
          {!!fullImage && (
            <Image
              source={socialImageSource(fullImage)}
              style={styles.fullImage}
              cachePolicy={isProtectedSocialMedia(fullImage) ? 'none' : 'memory-disk'}
              contentFit="contain"
            />
          )}
          {gallery.length > 1 && fullIndex !== null && (
            <>
              <Pressable
                onPress={() => stepGallery(-1)}
                disabled={fullIndex === 0}
                style={[styles.pageZone, styles.pageZoneLeft]}
                accessibilityLabel="上一张"
                accessibilityRole="button"
              />
              <Pressable
                onPress={() => stepGallery(1)}
                disabled={fullIndex >= gallery.length - 1}
                style={[styles.pageZone, styles.pageZoneRight]}
                accessibilityLabel="下一张"
                accessibilityRole="button"
              />
            </>
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
  backButton: {
    marginTop: Spacing.two,
    paddingHorizontal: Spacing.four,
    paddingVertical: Spacing.two,
    borderRadius: 14,
  },
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
  closeImage: { position: 'absolute', right: Spacing.four, zIndex: 2 },
  pageIndicator: { position: 'absolute', left: 0, right: 0, textAlign: 'center', zIndex: 1 },
  // 左右各 35% 作为翻页热区，中间留给图片本身（后续可接缩放手势）。
  pageZone: { position: 'absolute', top: 100, bottom: 100, width: '35%' },
  pageZoneLeft: { left: 0 },
  pageZoneRight: { right: 0 },
  fullImage: { width: '100%', height: '85%' },
});
