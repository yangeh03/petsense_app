import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { Alert, Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Shadows, Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { isProtectedSocialMedia, socialImageSource } from '@/services/api';
import type { Post } from '@/types/social';

/** 点赞/评论数格式化：千位缩写（1.2k），对齐 ins 观感 */
function formatCount(value: number): string {
  if (value >= 1000) {
    const k = value / 1000;
    return `${k >= 10 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, '')}k`;
  }
  return String(value);
}

/** 社区帖子卡（ins 风格）：自己的帖子带 ⋯ 删除菜单，大图、文案、点赞/评论。 */
export function PostCard({
  post,
  onLike,
  onOpenComments,
  onOpenAuthor,
  onOpenPost,
  onBookmark,
  onEdit,
  expanded = false,
  onDelete,
}: {
  post: Post;
  onLike: (post: Post) => void;
  onOpenComments: (post: Post) => void;
  onOpenAuthor: (post: Post) => void;
  onOpenPost?: (post: Post) => void;
  onBookmark?: (post: Post) => void;
  onEdit?: (post: Post) => void;
  expanded?: boolean;
  onDelete?: (post: Post) => void;
}) {
  const theme = useTheme();
  const liked = post.liked_by_me;
  const canDelete = post.isMine && !!onDelete;

  const openMenu = () => {
    if (!canDelete) return;
    Alert.alert(post.display_name, undefined, [
      ...(onEdit ? [{ text: '编辑帖子', onPress: () => onEdit(post) }] : []),
      {
        text: '删除帖子',
        style: 'destructive',
        onPress: () =>
          Alert.alert('删除帖子？', '删除后无法恢复。', [
            { text: '取消', style: 'cancel' },
            { text: '删除', style: 'destructive', onPress: () => onDelete?.(post) },
          ]),
      },
      { text: '取消', style: 'cancel' },
    ]);
  };

  return (
    <ThemedView type="backgroundElement" style={styles.card}>
      {/* 作者区与菜单按钮是兄弟节点：嵌套 Pressable 会把菜单合并进作者行，读屏无法单独触达。 */}
      <View style={styles.authorRow}>
        <Pressable
          style={styles.authorLink}
          onPress={() => onOpenAuthor(post)}
          accessibilityRole="button"
          accessibilityLabel={`查看 ${post.display_name} 的主页`}
        >
          <View style={[styles.authorAvatar, { backgroundColor: theme.tintBackground }]}>
            <ThemedText style={{ fontSize: 20 }}>{post.avatar_emoji || '🙂'}</ThemedText>
          </View>
          <View style={styles.authorTexts}>
            <ThemedText type="smallBold" style={styles.authorName}>
              {post.display_name}
            </ThemedText>
            <ThemedText type="small" themeColor="textSecondary" style={styles.meta}>
              {post.pet_name ? `${post.pet_emoji || '🐾'} ${post.pet_name} · ` : ''}
              {post.location_text ? `${post.location_text} · ` : ''}
              {timeAgo(post.created_at)}
              {post.updated_at ? ' · 已编辑' : ''}
            </ThemedText>
          </View>
        </Pressable>
        {canDelete && (
          <Pressable
            onPress={openMenu}
            hitSlop={8}
            accessibilityLabel="更多操作"
            accessibilityRole="button"
            style={styles.moreButton}
          >
            <SymbolView
              name={{ ios: 'ellipsis', android: 'more_vert', web: 'more_horiz' }}
              size={18}
              tintColor={theme.textSecondary}
            />
          </Pressable>
        )}
      </View>

      {(post.imageUrl || post.mediaUrl) && (
        <Pressable
          onPress={() => onOpenPost?.(post)}
          accessibilityLabel={
            (post.media?.length ?? 0) > 1
              ? `查看帖子详情，共 ${post.media!.length} 张图片`
              : '查看帖子详情'
          }
          style={styles.imageWrap}
        >
          <Image
            source={socialImageSource(
              post.mediaKind === 'gif' && post.mediaUrl ? post.mediaUrl : post.imageUrl,
            )}
            cachePolicy={isProtectedSocialMedia(post.imageUrl) ? 'none' : 'memory-disk'}
            style={styles.image}
            contentFit="cover"
            transition={150}
            autoplay={true}
          />
          {(post.media?.length ?? 0) > 1 && (
            <View style={[styles.mediaBadge, { backgroundColor: theme.text }]}>
              <SymbolView
                name={{
                  ios: 'square.on.square',
                  android: 'photo_library',
                  web: 'photo_library',
                }}
                size={12}
                tintColor={theme.background}
              />
              <ThemedText style={[styles.mediaBadgeText, { color: theme.background }]}>
                {post.media!.length}
              </ThemedText>
            </View>
          )}
        </Pressable>
      )}

      {post.caption ? (
        <Pressable onPress={() => onOpenPost?.(post)} accessibilityLabel="阅读帖子全文">
          <ThemedText type="small" style={styles.caption} numberOfLines={expanded ? undefined : 4}>
            {post.caption}
          </ThemedText>
        </Pressable>
      ) : null}

      <View style={styles.actionRow}>
        <View style={styles.actionGroup}>
          <ActionIcon
            icon={{
              ios: liked ? 'heart.fill' : 'heart',
              android: liked ? 'favorite' : 'favorite_border',
              web: liked ? 'favorite' : 'favorite_border',
            }}
            label={formatCount(post.likes)}
            color={liked ? theme.danger : theme.textSecondary}
            accessibilityLabel={liked ? '取消点赞' : '点赞'}
            onPress={() => onLike(post)}
          />
          <ActionIcon
            icon={{
              ios: 'bubble.right',
              android: 'chat_bubble_outline',
              web: 'chat_bubble_outline',
            }}
            label={formatCount(post.comments_count)}
            color={theme.textSecondary}
            accessibilityLabel="评论"
            onPress={() => onOpenComments(post)}
          />
          {/* 收藏放在左侧操作组：卡片右下角会被全局“创建”悬浮按钮盖住。 */}
          {!!onBookmark && (
            <ActionIcon
              icon={{
                ios: post.bookmarked_by_me ? 'bookmark.fill' : 'bookmark',
                android: post.bookmarked_by_me ? 'bookmark' : 'bookmark_border',
                web: post.bookmarked_by_me ? 'bookmark' : 'bookmark_border',
              }}
              label=""
              color={post.bookmarked_by_me ? theme.tint : theme.textSecondary}
              accessibilityLabel={post.bookmarked_by_me ? '取消收藏' : '收藏帖子'}
              onPress={() => onBookmark(post)}
            />
          )}
        </View>
      </View>
    </ThemedView>
  );
}

function ActionIcon({
  icon,
  label,
  color,
  accessibilityLabel,
  onPress,
}: {
  icon: Parameters<typeof SymbolView>[0]['name'];
  label: string;
  color: string;
  accessibilityLabel: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      style={({ pressed }) => [styles.action, pressed && { opacity: 0.6 }]}
    >
      <SymbolView name={icon} size={22} tintColor={color} />
      {label !== '0' && (
        <ThemedText type="small" style={{ color }}>
          {label}
        </ThemedText>
      )}
    </Pressable>
  );
}

export function timeAgo(iso: string) {
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (seconds < 60) return '刚刚';
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.floor(minutes)} 分钟前`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.floor(hours)} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 24,
    paddingVertical: Spacing.three,
    gap: Spacing.three,
    boxShadow: Shadows.card,
  },
  authorRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two + 4,
    paddingHorizontal: Spacing.three,
  },
  authorAvatar: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: 'center',
    justifyContent: 'center',
  },
  authorTexts: { gap: 2, flexShrink: 1 },
  authorLink: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two + 4, flex: 1 },
  authorName: { fontSize: 15, lineHeight: 20 },
  meta: { flexShrink: 1, fontSize: 11, lineHeight: 15 },
  moreButton: { marginLeft: 'auto', paddingHorizontal: Spacing.one },
  imageWrap: { position: 'relative' },
  image: { width: '100%', aspectRatio: 4 / 3, borderRadius: 16 },
  mediaBadge: {
    position: 'absolute',
    top: Spacing.two,
    right: Spacing.two,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: Spacing.two,
    paddingVertical: 3,
    borderRadius: 10,
    opacity: 0.85,
  },
  mediaBadgeText: { fontSize: 11, lineHeight: 14, fontWeight: '700' },
  caption: { paddingHorizontal: Spacing.three, lineHeight: 20 },
  actionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Spacing.three,
  },
  actionGroup: { flexDirection: 'row', gap: Spacing.four },
  action: { flexDirection: 'row', alignItems: 'center', gap: 6 },
});
