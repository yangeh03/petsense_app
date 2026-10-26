import * as Location from 'expo-location';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import {
  createNearbyQuery,
  disableDiscoveryLocation,
  fetchDiscoveryPreference,
  fetchNearbyPosts,
  fetchNearbyUsers,
  updateDiscoveryLocation,
} from '@/services/api';
import type { NearbyUser, Post } from '@/types/social';

type Position = { latitude: number; longitude: number; accuracyMeters: number };
type PreferenceState = 'loading' | 'ready' | 'failed';

class LocationPermissionError extends Error {
  constructor(
    message: string,
    readonly canAskAgain: boolean,
  ) {
    super(message);
  }
}

export default function NearbyScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const viewerId = user?.id ?? 0;
  const activeViewer = useRef(viewerId);
  const [radius, setRadius] = useState<1 | 5 | 10 | 20>(5);
  const [position, setPosition] = useState<Position | null>(null);
  const [positionOwner, setPositionOwner] = useState<number | null>(null);
  const [discoverable, setDiscoverable] = useState(false);
  const [preference, setPreference] = useState<{ owner: number; state: PreferenceState }>({
    owner: 0,
    state: 'ready',
  });
  // 偏好归属当前用户才算已加载；换账号后自动回到 loading，直到新用户的请求返回。
  const preferenceState: PreferenceState = !user
    ? 'ready'
    : preference.owner === viewerId
      ? preference.state
      : 'loading';
  const preferenceOwner =
    preference.owner === viewerId && preference.state === 'ready' ? viewerId : null;
  const [users, setUsers] = useState<NearbyUser[]>([]);
  const [posts, setPosts] = useState<Post[]>([]);
  const [section, setSection] = useState<'users' | 'posts'>('users');
  const [queryOwner, setQueryOwner] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [permissionBlocked, setPermissionBlocked] = useState(false);
  const [searched, setSearched] = useState(false);
  const [queryId, setQueryId] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [postCursor, setPostCursor] = useState<string | null>(null);

  useEffect(() => {
    activeViewer.current = viewerId;
  }, [viewerId]);

  const fetchPreference = useCallback(() => {
    void fetchDiscoveryPreference()
      .then((data) => {
        if (activeViewer.current !== viewerId) return;
        setDiscoverable(data.enabled);
        setPreference({ owner: viewerId, state: 'ready' });
      })
      .catch(() => {
        if (activeViewer.current === viewerId) setPreference({ owner: viewerId, state: 'failed' });
      });
  }, [viewerId]);

  useEffect(() => {
    if (user) fetchPreference();
  }, [user, fetchPreference]);

  const locate = async (): Promise<Position> => {
    const permission = await Location.requestForegroundPermissionsAsync();
    if (!permission.granted) {
      throw new LocationPermissionError(
        permission.canAskAgain
          ? '需要位置权限才能查找附近宠友。'
          : '位置权限已被拒绝。请在系统设置中允许 PetSense 使用期间定位，再回来重试。',
        permission.canAskAgain,
      );
    }
    const enabled = await Location.hasServicesEnabledAsync();
    if (!enabled) throw new Error('系统定位服务已关闭，请开启后重试。');
    const location = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    });
    const accuracyMeters = location.coords.accuracy ?? 1001;
    if (accuracyMeters > 1000) throw new Error('当前位置精度不足，请到开阔处重试。');
    return {
      latitude: location.coords.latitude,
      longitude: location.coords.longitude,
      accuracyMeters,
    };
  };

  const reportError = (exc: unknown, fallback: string) => {
    if (activeViewer.current !== viewerId) return;
    setPermissionBlocked(exc instanceof LocationPermissionError && !exc.canAskAgain);
    setError(exc instanceof Error ? exc.message : fallback);
  };

  const search = async () => {
    if (!user) {
      router.push('/login');
      return;
    }
    setLoading(true);
    setError('');
    setPermissionBlocked(false);
    try {
      const current = await locate();
      if (activeViewer.current !== viewerId) return;
      setPosition(current);
      setPositionOwner(viewerId);
      const created = await createNearbyQuery({ ...current, radiusKm: radius });
      const [data, nearbyPosts] = await Promise.all([
        fetchNearbyUsers(created.queryId),
        fetchNearbyPosts(created.queryId),
      ]);
      if (activeViewer.current !== viewerId) return;
      setQueryId(created.queryId);
      setQueryOwner(viewerId);
      setUsers(data.users);
      setPosts(nearbyPosts.posts);
      setNextCursor(data.page.nextCursor);
      setPostCursor(nearbyPosts.page.nextCursor);
      setSearched(true);
    } catch (exc) {
      reportError(exc, '查找附近宠友失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const toggleDiscoverable = async () => {
    if (!user) {
      router.push('/login');
      return;
    }
    if (preferenceState === 'failed') {
      setPreference({ owner: viewerId, state: 'loading' });
      fetchPreference();
      return;
    }
    setLoading(true);
    setError('');
    setPermissionBlocked(false);
    try {
      if (preferenceOwner === viewerId && discoverable) {
        await disableDiscoveryLocation();
        if (activeViewer.current === viewerId) setDiscoverable(false);
      } else {
        const current = positionOwner === viewerId && position ? position : await locate();
        if (activeViewer.current !== viewerId) return;
        setPosition(current);
        setPositionOwner(viewerId);
        await updateDiscoveryLocation({ ...current, enabled: true });
        if (activeViewer.current === viewerId) {
          setDiscoverable(true);
          setPreference({ owner: viewerId, state: 'ready' });
        }
      }
    } catch (exc) {
      reportError(exc, '附近设置保存失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const loadMore = async () => {
    const cursor = section === 'users' ? nextCursor : postCursor;
    if (!queryId || !cursor || loading || queryOwner !== viewerId) return;
    setLoading(true);
    try {
      if (section === 'users') {
        const data = await fetchNearbyUsers(queryId, cursor);
        if (activeViewer.current !== viewerId) return;
        setUsers((current) => [...current, ...data.users]);
        setNextCursor(data.page.nextCursor);
      } else {
        const data = await fetchNearbyPosts(queryId, cursor);
        if (activeViewer.current !== viewerId) return;
        setPosts((current) => [...current, ...data.posts]);
        setPostCursor(data.page.nextCursor);
      }
      setError('');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载更多失败，请重新查找');
    } finally {
      setLoading(false);
    }
  };

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <View style={styles.content}>
        <View style={styles.header}>
          <Pressable onPress={() => router.back()} accessibilityLabel="返回社区">
            <SymbolView
              name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
              size={24}
              tintColor={theme.tint}
            />
          </Pressable>
          <ThemedText type="subtitle">附近的人</ThemedText>
          <View style={styles.spacer} />
        </View>
        <ThemedText type="small" themeColor="textSecondary">
          仅在使用时定位。附近动态来自主动开启位置公开的宠友；其他人只看到距离范围，不会看到你的坐标。
        </ThemedText>
        <View style={styles.radiusRow}>
          {([1, 5, 10, 20] as const).map((item) => (
            <Pressable
              key={item}
              onPress={() => setRadius(item)}
              style={[
                styles.radiusButton,
                { backgroundColor: radius === item ? theme.tint : theme.backgroundSelected },
              ]}
            >
              <ThemedText type="small" style={{ color: radius === item ? '#ffffff' : theme.text }}>
                {item} km
              </ThemedText>
            </Pressable>
          ))}
        </View>
        <Pressable
          onPress={() => void search()}
          disabled={loading}
          style={[styles.primary, { backgroundColor: theme.tint, opacity: loading ? 0.6 : 1 }]}
        >
          <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
            查找附近宠友
          </ThemedText>
        </Pressable>
        <Pressable
          onPress={() => void toggleDiscoverable()}
          disabled={loading || preferenceState === 'loading'}
          accessibilityLabel={
            preferenceState === 'failed'
              ? '附近设置加载失败，点击重试'
              : discoverable
                ? '关闭附近宠友发现我'
                : '允许附近宠友发现我'
          }
          style={[styles.toggle, { borderColor: theme.backgroundSelected }]}
        >
          <ThemedText type="smallBold">
            {preferenceState === 'loading'
              ? '正在读取附近设置…'
              : preferenceState === 'failed'
                ? '附近设置加载失败 · 点击重试'
                : preferenceOwner === viewerId && discoverable
                  ? '附近宠友可以发现我 · 点击关闭'
                  : '允许附近宠友发现我 · 当前关闭'}
          </ThemedText>
        </Pressable>
        {!!error && (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {error}
          </ThemedText>
        )}
        {permissionBlocked && Platform.OS !== 'web' && (
          <Pressable
            onPress={() => void Linking.openSettings()}
            style={[styles.toggle, { borderColor: theme.tint }]}
            accessibilityLabel="打开系统设置"
          >
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              打开系统设置
            </ThemedText>
          </Pressable>
        )}
        {loading && <ActivityIndicator color={theme.tint} />}
        <View style={styles.radiusRow}>
          {(['users', 'posts'] as const).map((item) => (
            <Pressable
              key={item}
              onPress={() => setSection(item)}
              style={[
                styles.radiusButton,
                { backgroundColor: section === item ? theme.tint : theme.backgroundSelected },
              ]}
            >
              <ThemedText type="small" style={{ color: section === item ? '#ffffff' : theme.text }}>
                {item === 'users' ? '附近宠友' : '附近动态'}
              </ThemedText>
            </Pressable>
          ))}
        </View>
        {searched &&
          queryOwner === viewerId &&
          !loading &&
          (section === 'users' ? users.length === 0 : posts.length === 0) && (
            <ThemedText type="small" themeColor="textSecondary">
              {section === 'users'
                ? '当前范围内还没有公开位置的宠友'
                : '当前范围内还没有附近宠友的动态'}
            </ThemedText>
          )}
        {section === 'users' ? (
          <FlatList
            data={queryOwner === viewerId ? users : []}
            keyExtractor={(item) => String(item.id)}
            renderItem={({ item }) => (
              <Pressable
                onPress={() => router.push(`/user?id=${item.id}`)}
                style={[styles.row, { borderColor: theme.backgroundSelected }]}
              >
                <ThemedText type="smallBold">
                  {item.avatar_emoji} {item.display_name}
                </ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  {item.distanceBand}
                </ThemedText>
              </Pressable>
            )}
            ListFooterComponent={
              queryOwner === viewerId && nextCursor ? (
                <Pressable onPress={() => void loadMore()}>
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    加载更多
                  </ThemedText>
                </Pressable>
              ) : null
            }
          />
        ) : (
          <FlatList
            data={queryOwner === viewerId ? posts : []}
            keyExtractor={(item) => String(item.id)}
            renderItem={({ item }) => (
              <Pressable
                onPress={() => router.push(`/post?id=${item.id}`)}
                style={[styles.postRow, { borderColor: theme.backgroundSelected }]}
              >
                <ThemedText type="smallBold">
                  {item.avatar_emoji} {item.display_name}
                </ThemedText>
                <ThemedText type="small" numberOfLines={2}>
                  {item.caption || '查看图片动态'}
                </ThemedText>
              </Pressable>
            )}
            ListFooterComponent={
              queryOwner === viewerId && postCursor ? (
                <Pressable onPress={() => void loadMore()}>
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    加载更多
                  </ThemedText>
                </Pressable>
              ) : null
            }
          />
        )}
      </View>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: {
    flex: 1,
    padding: Spacing.four,
    gap: Spacing.three,
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  spacer: { width: 24 },
  radiusRow: { flexDirection: 'row', gap: Spacing.two },
  radiusButton: { padding: Spacing.two, borderRadius: 12 },
  primary: { padding: Spacing.three, alignItems: 'center', borderRadius: 14 },
  toggle: { padding: Spacing.three, borderWidth: 1, borderRadius: 14 },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: Spacing.three,
    borderBottomWidth: 1,
  },
  postRow: { gap: Spacing.one, paddingVertical: Spacing.three, borderBottomWidth: 1 },
});
