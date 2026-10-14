import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Shadows, Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import type { Post } from '@/types/social';

/** 社区帖子卡：作者 / 宠物 / 图片 / 文案 / 点赞评论。 */
export function PostCard({
  post,
  onLike,
  onOpenComments,
  onOpenAuthor,
  onDelete,
}: {
  post: Post;
  onLike: (post: Post) => void;
  onOpenComments: (post: Post) => void;
  onOpenAuthor: (post: Post) => void;
  onDelete?: (post: Post) => void;
}) {
  const theme = useTheme();
  const liked = post.liked_by_me;

  return (
    <ThemedView type="backgroundElement" style={styles.card}>
      <Pressable style={styles.authorRow} onPress={() => onOpenAuthor(post)}>
        <View style={[styles.authorAvatar, { backgroundColor: theme.tintBackground }]}>
          <ThemedText style={{ fontSize: 20 }}>{post.avatar_emoji || '🙂'}</ThemedText>
        </View>
        <View style={styles.authorTexts}>
          <ThemedText type="smallBold">{post.display_name}</ThemedText>
          <ThemedText type="small" themeColor="textSecondary" style={styles.meta}>
            {post.pet_name ? `${post.pet_emoji || '🐾'} ${post.pet_name} · ` : ''}
            {timeAgo(post.created_at)}
            {post.location_text ? ` · ${post.location_text}` : ''}
          </ThemedText>
        </View>
        {post.isMine && onDelete && (
          <Pressable
            onPress={() => onDelete(post)}
            hitSlop={8}
            accessibilityLabel="删除帖子"
            style={styles.deleteButton}
          >
            <SymbolView
              name={{ ios: 'trash', android: 'delete_outline', web: 'delete_outline' }}
              size={16}
              tintColor={theme.textSecondary}
            />
          </Pressable>
        )}
      </Pressable>

      <Image
        source={{ uri: absoluteUrl(post.imageUrl) }}
        style={styles.image}
        contentFit="cover"
        transition={150}
      />

      {post.caption ? (
        <ThemedText type="small" style={styles.caption} numberOfLines={4}>
          {post.caption}
        </ThemedText>
      ) : null}

      <View style={styles.actionRow}>
        <ActionIcon
          icon={{
            ios: liked ? 'heart.fill' : 'heart',
            android: liked ? 'favorite' : 'favorite_border',
            web: liked ? 'favorite' : 'favorite_border',
          }}
          label={String(post.likes)}
          color={liked ? theme.danger : theme.textSecondary}
          onPress={() => onLike(post)}
        />
        <ActionIcon
          icon={{
            ios: 'bubble.right',
            android: 'chat_bubble_outline',
            web: 'chat_bubble_outline',
          }}
          label={String(post.comments_count)}
          color={theme.textSecondary}
          onPress={() => onOpenComments(post)}
        />
      </View>
    </ThemedView>
  );
}

function ActionIcon({
  icon,
  label,
  color,
  onPress,
}: {
  icon: Parameters<typeof SymbolView>[0]['name'];
  label: string;
  color: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.action, pressed && { opacity: 0.6 }]}
    >
      <SymbolView name={icon} size={20} tintColor={color} />
      {label !== '0' && (
        <ThemedText type="small" style={{ color }}>
          {label}
        </ThemedText>
      )}
    </Pressable>
  );
}

function timeAgo(iso: string) {
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
    borderRadius: 20,
    paddingVertical: Spacing.three,
    gap: Spacing.three,
    boxShadow: Shadows.card,
  },
  authorRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    paddingHorizontal: Spacing.three,
  },
  authorAvatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  authorTexts: { gap: 2, flexShrink: 1 },
  deleteButton: { marginLeft: 'auto', paddingLeft: Spacing.one },
  meta: { flexShrink: 1 },
  image: { width: '100%', aspectRatio: 4 / 3, borderRadius: 14 },
  caption: { paddingHorizontal: Spacing.three },
  actionRow: { flexDirection: 'row', gap: Spacing.four, paddingHorizontal: Spacing.three },
  action: { flexDirection: 'row', alignItems: 'center', gap: 6 },
});
