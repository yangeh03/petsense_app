import { SymbolView } from 'expo-symbols';
import { useEffect, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchFollowList, toggleFollow } from '@/services/api';
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
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  // null = 加载中
  const [users, setUsers] = useState<FollowListUser[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchFollowList(userId, kind)
      .then((data) => {
        if (!cancelled) setUsers(data.users);
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [visible, userId, kind]);

  const loading = users === null;

  const toggle = (target: FollowListUser) => {
    if (!user) return;
    setUsers(
      (current) =>
        current?.map((item) =>
          item.id === target.id ? { ...item, is_following: !item.is_following } : item,
        ) ?? current,
    );
    toggleFollow(target.id).catch(() => {});
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
                    <View style={[styles.avatar, { backgroundColor: theme.tintBackground }]}>
                      <ThemedText style={{ fontSize: 18 }}>{item.avatar_emoji || '🙂'}</ThemedText>
                    </View>
                    <ThemedText type="smallBold" style={styles.name}>
                      {item.display_name}
                    </ThemedText>
                    {!isSelf && (
                      <Pressable
                        onPress={() => toggle(item)}
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
