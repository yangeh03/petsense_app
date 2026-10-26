import { AppIcon } from '@/components/ui/icon';
import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';

import { DeviceCard } from '@/components/device-card';
import { FollowListModal } from '@/components/social/follow-list-modal';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { usePetChanges } from '@/hooks/use-pet-changes';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl, fetchUserProfile } from '@/services/api';
import type { UserProfile } from '@/types/social';
import { speciesLabel } from '@/services/pet-profile';

/** 我的页：用户卡 + 三格统计 + 宠物列表（含电量/在线）+ 设备诊断入口。 */
export default function ProfileScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const [loadedProfile, setProfile] = useState<UserProfile | null>(null);
  const profile = loadedProfile?.id === user?.id ? loadedProfile : null;
  const sequence = useRef(0);
  const [profileError, setProfileError] = useState('');
  const [followKind, setFollowKind] = useState<'followers' | 'following' | null>(null);

  const load = useCallback(async () => {
    const current = ++sequence.current;
    if (!user) return;
    try {
      const data = await fetchUserProfile(user.id);
      if (current !== sequence.current) return;
      setProfile(data.profile);
      setProfileError('');
    } catch {
      if (current === sequence.current) setProfileError('宠物资料加载失败，点击重试');
    }
  }, [user]);

  useFocusEffect(
    useCallback(() => {
      void load();
      return () => {
        sequence.current++;
      };
    }, [load]),
  );
  usePetChanges(load);

  const confirmLogout = () => {
    Alert.alert('退出登录', '确定要退出当前账号吗？', [
      { text: '取消', style: 'cancel' },
      {
        text: '退出',
        style: 'destructive',
        onPress: () => void logout().then(() => setProfile(null)),
      },
    ]);
  };

  if (loading) {
    return (
      <View style={[styles.centerWrap, { backgroundColor: theme.background }]}>
        <ActivityIndicator color={theme.tint} />
      </View>
    );
  }

  if (!user) {
    return (
      <ScrollView style={[styles.scrollView, { backgroundColor: theme.background }]}>
        <ThemedView style={[styles.centerWrap, { paddingTop: insets.top + Spacing.four }]}>
          <ThemedText type="subtitle">我的</ThemedText>
          <ThemedView type="backgroundElement" style={styles.guestCard}>
            <ThemedText style={styles.guestEmoji}>🐾</ThemedText>
            <ThemedText type="smallBold">登录 PetSense</ThemedText>
            <ThemedText type="small" themeColor="textSecondary" style={styles.guestText}>
              登录后可以添加宠物、发帖和别的宠物家长互动
            </ThemedText>
            <Pressable
              onPress={() => router.push('/login')}
              style={({ pressed }) => [
                styles.primaryButton,
                { backgroundColor: theme.tint },
                pressed && { opacity: 0.85 },
              ]}
            >
              <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
                去登录 / 注册
              </ThemedText>
            </Pressable>
          </ThemedView>
        </ThemedView>
      </ScrollView>
    );
  }

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.four }]}>
        {/* 用户卡 */}
        <ThemedView type="backgroundElement" style={styles.userCard}>
          <View style={[styles.userAvatar, { backgroundColor: theme.tintBackground }]}>
            <ThemedText style={{ fontSize: 32 }}>{user.avatarEmoji}</ThemedText>
          </View>
          <View style={styles.userTexts}>
            <ThemedText type="smallBold" style={styles.userName}>
              {user.displayName}
            </ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              @{user.username}
            </ThemedText>
          </View>
          <Pressable
            onPress={confirmLogout}
            hitSlop={8}
            accessibilityRole="button"
            accessibilityLabel="退出登录"
          >
            <ThemedText type="small" style={{ color: theme.textSecondary }}>
              退出
            </ThemedText>
          </Pressable>
        </ThemedView>

        {/* 三格统计 */}
        <ThemedView type="backgroundElement" style={styles.statsRow}>
          <StatCell
            value={profile?.posts_count ?? 0}
            label="帖子"
            onPress={() => router.push(`/user?id=${user.id}`)}
          />
          <StatCell value={profile?.followers ?? 0} label="粉丝" />
          <StatCell value={profile?.following ?? 0} label="关注" />
        </ThemedView>

        {/* 我的设备（认领/解绑项圈） */}
        <DeviceCard />
        <Pressable onPress={() => router.push('/collect')} accessibilityRole="button">
          <ThemedView type="backgroundElement" style={styles.menuRow}>
            <AppIcon name="video-outline" size={20} color={theme.tint} />
            <ThemedText type="default" style={styles.menuLabel}>
              录像标注与校准
            </ThemedText>
            <AppIcon name="chevron-right" size={14} color={theme.textSecondary} />
          </ThemedView>
        </Pressable>

        {/* 我的宠物 */}
        <View style={styles.sectionHeader}>
          <ThemedText type="smallBold">宠物档案</ThemedText>
          {profile && profile.pets.length === 0 && (
            <Pressable onPress={() => router.push('/setup')}>
              <ThemedText type="small" style={{ color: theme.tint }}>
                添加宠物
              </ThemedText>
            </Pressable>
          )}
        </View>
        {!!profileError && (
          <Pressable onPress={() => void load()}>
            <ThemedText type="small" style={{ color: theme.danger }}>
              {profileError}
            </ThemedText>
          </Pressable>
        )}
        {!profile && !profileError && <ActivityIndicator color={theme.tint} />}
        {(profile?.pets ?? []).map((pet) => (
          <Pressable
            key={pet.id}
            accessibilityRole="button"
            accessibilityLabel={`管理 ${pet.name} 的宠物资料`}
            onPress={() => router.push('/pet')}
          >
            <ThemedView type="backgroundElement" style={styles.petCard}>
              <View style={[styles.petAvatar, { backgroundColor: theme.tintBackground }]}>
                {pet.avatarImageUrl ? (
                  <Image
                    source={{ uri: absoluteUrl(pet.avatarImageUrl) }}
                    style={styles.petAvatarImage}
                    contentFit="cover"
                  />
                ) : (
                  <ThemedText style={{ fontSize: 26 }}>{pet.avatar_emoji}</ThemedText>
                )}
              </View>
              <View style={styles.petTexts}>
                <ThemedText type="smallBold" style={styles.petName}>
                  {pet.name}
                </ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  {speciesLabel(pet.species)} · {pet.breed || '品种未填写'}
                </ThemedText>
                <ThemedText type="small" style={{ color: theme.tint }}>
                  查看与编辑资料
                </ThemedText>
              </View>
              {pet.batteryPercent != null ? (
                <View style={styles.petBattery}>
                  <View
                    style={[
                      styles.batteryDot,
                      { backgroundColor: pet.online ? theme.online : theme.textSecondary },
                    ]}
                  />
                  <ThemedText type="small" themeColor="textSecondary">
                    {pet.batteryPercent}%
                  </ThemedText>
                </View>
              ) : null}
              <SymbolView
                name={{ ios: 'chevron.right', android: 'chevron_right', web: 'chevron_right' }}
                size={16}
                tintColor={theme.textSecondary}
              />
            </ThemedView>
          </Pressable>
        ))}

        <FollowListModal
          visible={followKind !== null && profile !== null}
          userId={user.id}
          kind={followKind ?? 'followers'}
          onClose={() => setFollowKind(null)}
        />

        {/* 设备诊断入口 */}
        <Pressable onPress={() => router.push('/monitor')}>
          <ThemedView type="backgroundElement" style={styles.menuRow}>
            <AppIcon name="heart-pulse" size={20} color={theme.tint} />
            <ThemedText type="default" style={styles.menuLabel}>
              设备诊断（工程师视图）
            </ThemedText>
            <AppIcon name="chevron-right" size={14} color={theme.textSecondary} />
          </ThemedView>
        </Pressable>
      </ThemedView>
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
  return (
    <Pressable style={styles.statCell} onPress={onPress} disabled={!onPress}>
      <ThemedText style={styles.statValue}>{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  scrollView: { flex: 1 },
  // 水平边距统一放在 contentContainer（滚动容器级），兼容各端渲染差异
  contentContainer: { paddingHorizontal: Spacing.four },
  // 宽屏内容居中由 container 的 maxWidth + alignSelf 完成
  container: {
    flexGrow: 1,
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
    paddingBottom: BottomTabInset + Spacing.five,
    gap: Spacing.three,
  },
  centerWrap: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: Spacing.five,
    backgroundColor: 'transparent',
  },
  guestCard: {
    width: '100%',
    maxWidth: MaxContentWidth,
    borderRadius: 22,
    padding: Spacing.five,
    alignItems: 'center',
    gap: Spacing.two,
  },
  guestEmoji: { fontSize: 42 },
  guestText: { textAlign: 'center' },
  primaryButton: {
    marginTop: Spacing.two,
    borderRadius: 16,
    paddingHorizontal: Spacing.five,
    paddingVertical: 10,
  },
  userCard: {
    width: '100%',
    borderRadius: 22,
    padding: Spacing.four,
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    boxShadow: Shadows.card,
  },
  userAvatar: {
    width: 64,
    height: 64,
    borderRadius: 32,
    alignItems: 'center',
    justifyContent: 'center',
  },
  userTexts: { gap: 2, flexShrink: 1 },
  userName: { fontSize: 18 },
  statsRow: {
    width: '100%',
    flexDirection: 'row',
    borderRadius: 22,
    paddingVertical: Spacing.three,
    boxShadow: Shadows.card,
  },
  statCell: { flex: 1, alignItems: 'center', gap: 2 },
  statValue: { fontSize: 22, fontWeight: '700', lineHeight: 26 },
  sectionHeader: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: Spacing.one,
  },
  petCard: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    borderRadius: 18,
    padding: Spacing.three,
    boxShadow: Shadows.card,
  },
  petAvatarImage: { width: '100%', height: '100%' },
  aiButton: { padding: Spacing.one },
  petAvatar: {
    width: 52,
    height: 52,
    borderRadius: 26,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  petTexts: { gap: 2, flexShrink: 1 },
  petName: { fontSize: 16 },
  petBattery: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  batteryDot: { width: 8, height: 8, borderRadius: 4 },
  menuRow: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    borderRadius: 18,
    padding: Spacing.three,
  },
  menuLabel: { marginRight: 'auto' },
});
