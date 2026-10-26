import { SymbolView } from 'expo-symbols';
import { useEffect, useState } from 'react';
import { useRouter } from 'expo-router';
import { Modal, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchFollowList, setFollow } from '@/services/api';
import type { FollowListUser } from '@/types/social';

type Kind = 'followers' | 'following';

/** 粉丝/关注列表弹窗：可在列表内直接关注/取关。 */
export function FollowListModal({
  visible,
  userId,
  kind,
  onClose,
}: {
  visible: boolean;
  userId: number;
  kind: Kind;
  onClose: () => void;
}) {
  const theme = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  // null = 加载中
  const [users, setUsers] = useState<FollowListUser[] | null>(null);
  const [error, setError] = useState('');
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadedKey, setLoadedKey] = useState('');
  const currentKey = `${userId}:${kind}:${visible}`;

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    fetchFollowList(userId, kind)
      .then((data) => {
        if (!cancelled) {
          setUsers(data.users);
          setNextCursor(data.page.nextCursor);
          setLoadedKey(currentKey);
          setError('');
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUsers([]);
          setNextCursor(null);
          setLoadedKey(currentKey);
          setError('列表加载失败，请关闭后重试');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [visible, userId, kind, currentKey]);

  const loading = users === null || loadedKey !== currentKey;

  const toggle = (target: FollowListUser) => {
    if (!user || pendingId !== null) return;
    setPendingId(target.id);
    setError('');
    setFollow(target.id, !target.is_following)
      .then((result) =>
        setUsers(
          (current) =>
            current?.map((item) =>
              item.id === target.id ? { ...item, is_following: result.following } : item,
            ) ?? current,
        ),
      )
      .catch(() => setError('关注操作失败，请重试'))
      .finally(() => setPendingId(null));
  };

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const data = await fetchFollowList(userId, kind, nextCursor);
      setUsers((current) => [
        ...(current ?? []),
        ...data.users.filter((item) => !current?.some((previous) => previous.id === item.id)),
      ]);
      setNextCursor(data.page.nextCursor);
      setError('');
    } catch {
      setError('更多用户加载失败，请重试');
    } finally {
      setLoadingMore(false);
    }
  };

  const title = kind === 'followers' ? '粉丝' : '关注';

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable
          style={[
            styles.sheet,
            { backgroundColor: theme.background, paddingBottom: insets.bottom + Spacing.three },
          ]}
          onPress={(event) => event.stopPropagation()}
        >
          <View style={styles.header}>
            <ThemedText type="subtitle" style={styles.title}>
              {title}
            </ThemedText>
            <Pressable onPress={onClose} hitSlop={10} accessibilityLabel="关闭列表">
              <SymbolView
                name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
                size={26}
                tintColor={theme.textSecondary}
              />
            </Pressable>
          </View>

          <ScrollView style={styles.list} contentContainerStyle={{ gap: Spacing.two }}>
            {!!error && !loading && (
              <ThemedText type="small" style={{ color: theme.danger }}>
                {error}
              </ThemedText>
            )}
            {loading ? (
              <ThemedText type="small" themeColor="textSecondary" style={styles.empty}>
                加载中…
              </ThemedText>
            ) : users.length === 0 ? (
              <ThemedText type="small" themeColor="textSecondary" style={styles.empty}>
                暂无{title}用户
              </ThemedText>
            ) : (
              users.map((item) => {
                const isSelf = user?.id === item.id;
                return (
                  <View
                    key={item.id}
                    style={[styles.row, { backgroundColor: theme.backgroundElement }]}
                  >
                    <Pressable
                      onPress={() => {
                        onClose();
                        router.push(`/user?id=${item.id}`);
                      }}
                      style={styles.userLink}
                      accessibilityLabel={`查看 ${item.display_name} 的主页`}
                    >
                      <View style={[styles.avatar, { backgroundColor: theme.tintBackground }]}>
                        <ThemedText style={{ fontSize: 18 }}>
                          {item.avatar_emoji || '🙂'}
                        </ThemedText>
                      </View>
                      <ThemedText type="smallBold" style={styles.name}>
                        {item.display_name}
                      </ThemedText>
                    </Pressable>
                    {!isSelf && (
                      <Pressable
                        onPress={() => toggle(item)}
                        disabled={pendingId !== null}
                        style={({ pressed }) => [
                          styles.followButton,
                          {
                            backgroundColor: item.is_following
                              ? theme.backgroundSelected
                              : theme.tint,
                          },
                          pressed && { opacity: 0.7 },
                        ]}
                      >
                        <ThemedText
                          type="smallBold"
                          style={{
                            color: item.is_following ? theme.textSecondary : '#ffffff',
                            fontSize: 12,
                            lineHeight: 16,
                          }}
                        >
                          {item.is_following ? '已关注' : '关注'}
                        </ThemedText>
                      </Pressable>
                    )}
                  </View>
                );
              })
            )}
            {!!nextCursor && (
              <Pressable onPress={() => void loadMore()}>
                <ThemedText type="small" style={{ color: theme.tint }}>
                  {loadingMore ? '加载中…' : '加载更多'}
                </ThemedText>
              </Pressable>
            )}
          </ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(20, 12, 6, 0.35)',
    justifyContent: 'flex-end',
  },
  sheet: {
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.three,
    maxHeight: '70%',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: Spacing.three,
  },
  title: { fontSize: 22 },
  list: { flexGrow: 0 },
  empty: { textAlign: 'center', paddingVertical: Spacing.four },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    borderRadius: 16,
    padding: Spacing.two + 4,
  },
  userLink: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  avatar: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  name: { flex: 1 },
  followButton: { borderRadius: 12, paddingHorizontal: 12, paddingVertical: 6 },
});
