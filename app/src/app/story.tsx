import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  AppState,
  Modal,
  Pressable,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import {
  deleteStory,
  fetchStoryViewers,
  fetchUserStories,
  markStoryViewed,
  socialImageSource,
} from '@/services/api';
import type { Story } from '@/types/social';

export default function StoryScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const params = useLocalSearchParams<{ userId: string }>();
  const userId = Number(params.userId);
  const [stories, setStories] = useState<Story[]>([]);
  const [index, setIndex] = useState(0);
  const [progress, setProgress] = useState(0);
  const [ready, setReady] = useState(false);
  const [paused, setPaused] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [viewers, setViewers] = useState<
    { id: number; display_name: string; avatar_emoji: string; viewed_at: string }[] | null
  >(null);
  const viewerKey = `${user?.id ?? 'guest'}:${userId}`;
  const visibleStories = loadedFor === viewerKey ? stories : [];
  const story = visibleStories[index];
  const progressRef = useRef(0);
  const activeViewerKey = useRef(viewerKey);
  const viewedIds = useRef(new Set<number>());
  const invalidUserId = !Number.isSafeInteger(userId) || userId <= 0;

  useEffect(() => {
    activeViewerKey.current = viewerKey;
  }, [viewerKey]);

  useEffect(() => {
    let active = true;
    if (invalidUserId) return;
    void fetchUserStories(userId)
      .then((data) => {
        if (active) {
          setStories(data.stories);
          setViewers(null);
          setReady(false);
          setIndex(0);
          progressRef.current = 0;
          setProgress(0);
          setLoadedFor(viewerKey);
          setError('');
        }
      })
      .catch((exc) => {
        if (active) {
          setStories([]);
          setViewers(null);
          setLoadedFor(viewerKey);
          setError(exc instanceof Error ? exc.message : '故事加载失败');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [userId, invalidUserId, viewerKey]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (state) =>
      setPaused(state !== 'active'),
    );
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (!ready || paused || !story) return;
    const timer = setInterval(() => {
      progressRef.current = Math.min(1, progressRef.current + 0.02);
      setProgress(progressRef.current);
      // 真正可见且未暂停地展示满 1 秒（进度 0.2）才算已读；预加载、刚解码都不算。
      if (progressRef.current >= 0.2 && user && !viewedIds.current.has(story.id)) {
        viewedIds.current.add(story.id);
        void markStoryViewed(story.id).catch(() => {});
      }
      if (progressRef.current >= 1) {
        progressRef.current = 0;
        setProgress(0);
        setReady(false);
        if (index + 1 < visibleStories.length) setIndex(index + 1);
        else router.back();
      }
    }, 100);
    return () => clearInterval(timer);
  }, [ready, paused, story, index, visibleStories.length, router, user]);

  const showStory = (nextIndex: number) => {
    progressRef.current = 0;
    setProgress(0);
    setReady(false);
    setError('');
    setIndex(nextIndex);
  };

  const onLoaded = () => {
    if (!story || loadedFor !== viewerKey || activeViewerKey.current !== viewerKey) return;
    setReady(true);
  };

  const removeStory = () => {
    if (!story) return;
    Alert.alert('删除故事？', '删除后无法恢复。', [
      { text: '取消', style: 'cancel' },
      {
        text: '删除',
        style: 'destructive',
        onPress: () => {
          void deleteStory(story.id)
            .then(() => {
              const next = visibleStories.filter((item) => item.id !== story.id);
              setStories(next);
              if (!next.length) router.back();
              else showStory(Math.min(index, next.length - 1));
            })
            .catch((exc) => setError(exc instanceof Error ? exc.message : '删除失败'));
        },
      },
    ]);
  };

  const openViewers = async () => {
    if (!story) return;
    const requestKey = viewerKey;
    try {
      const result = await fetchStoryViewers(story.id);
      if (activeViewerKey.current === requestKey) setViewers(result.viewers);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '查看者加载失败');
    }
  };

  return (
    <ThemedView
      style={[
        styles.screen,
        {
          backgroundColor: theme.background,
          paddingTop: insets.top + Spacing.two,
          paddingBottom: insets.bottom + Spacing.two,
        },
      ]}
    >
      {invalidUserId ? (
        <ThemedText type="small" style={styles.empty}>
          故事链接无效
        </ThemedText>
      ) : loading || loadedFor !== viewerKey ? (
        <ActivityIndicator color={theme.tint} />
      ) : !story ? (
        <Pressable onPress={() => router.back()} style={styles.empty}>
          <ThemedText type="small" themeColor="textSecondary">
            {error || '故事已过期或不可见'}
          </ThemedText>
          <ThemedText type="small" style={{ color: theme.tint }}>
            返回社区
          </ThemedText>
        </Pressable>
      ) : (
        <>
          <View style={styles.progressRow}>
            {visibleStories.map((item, itemIndex) => (
              <View
                key={item.id}
                style={[styles.progressTrack, { backgroundColor: theme.backgroundSelected }]}
              >
                <View
                  style={[
                    styles.progressFill,
                    {
                      backgroundColor: theme.tint,
                      width: `${itemIndex < index ? 100 : itemIndex > index ? 0 : progress * 100}%`,
                    },
                  ]}
                />
              </View>
            ))}
          </View>
          <View style={styles.header}>
            <Pressable onPress={() => router.push(`/user?id=${story.user_id}`)}>
              <ThemedText type="smallBold">
                {story.avatar_emoji} {story.display_name}
              </ThemedText>
            </Pressable>
            <Pressable onPress={() => router.back()} accessibilityLabel="关闭故事">
              <SymbolView
                name={{ ios: 'xmark', android: 'close', web: 'close' }}
                size={24}
                tintColor={theme.text}
              />
            </Pressable>
          </View>
          {user?.id === story.user_id && (
            <View style={styles.authorActions}>
              <Pressable onPress={() => void openViewers()}>
                <ThemedText type="small" style={{ color: theme.tint }}>
                  查看者
                </ThemedText>
              </Pressable>
              <Pressable onPress={removeStory}>
                <ThemedText type="small" style={{ color: theme.danger }}>
                  删除
                </ThemedText>
              </Pressable>
            </View>
          )}
          <View style={styles.viewer}>
            <Image
              key={story.id}
              source={socialImageSource(story.imageUrl)}
              cachePolicy="none"
              style={styles.image}
              contentFit="contain"
              onLoad={onLoaded}
              onError={() => {
                setReady(false);
                setError('图片加载失败，请检查网络后重试');
              }}
            />
            <View style={styles.controls}>
              <Pressable
                style={styles.touchZone}
                onPress={() => showStory(Math.max(0, index - 1))}
                onPressIn={() => setPaused(true)}
                onPressOut={() => setPaused(false)}
              />
              <Pressable
                style={styles.touchZone}
                onPress={() => {
                  if (index + 1 < visibleStories.length) showStory(index + 1);
                  else router.back();
                }}
                onPressIn={() => setPaused(true)}
                onPressOut={() => setPaused(false)}
              />
            </View>
          </View>
          <ThemedText style={styles.caption}>{story.caption}</ThemedText>
          {!!error && (
            <ThemedText type="small" style={[styles.error, { color: theme.danger }]}>
              {error}
            </ThemedText>
          )}
        </>
      )}
      <Modal
        visible={loadedFor === viewerKey && viewers !== null}
        transparent
        onRequestClose={() => setViewers(null)}
      >
        <Pressable style={styles.viewersBackdrop} onPress={() => setViewers(null)}>
          <ThemedView style={[styles.viewersSheet, { backgroundColor: theme.background }]}>
            <ThemedText type="smallBold">故事查看者</ThemedText>
            {viewers?.length ? (
              viewers.map((item) => (
                <ThemedText key={item.id} type="small">
                  {item.avatar_emoji} {item.display_name}
                </ThemedText>
              ))
            ) : (
              <ThemedText type="small" themeColor="textSecondary">
                暂无查看者
              </ThemedText>
            )}
          </ThemedView>
        </Pressable>
      </Modal>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: Spacing.two },
  progressRow: { flexDirection: 'row', gap: Spacing.one, marginHorizontal: Spacing.three },
  progressTrack: { flex: 1, height: 3, borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: 3 },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    margin: Spacing.three,
  },
  authorActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: Spacing.three,
    paddingHorizontal: Spacing.three,
  },
  viewersBackdrop: { flex: 1, justifyContent: 'flex-end' },
  viewersSheet: {
    padding: Spacing.four,
    gap: Spacing.two,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
  },
  viewer: { flex: 1 },
  image: { width: '100%', height: '100%' },
  controls: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, flexDirection: 'row' },
  touchZone: { flex: 1 },
  caption: { padding: Spacing.three, textAlign: 'center' },
  error: { paddingHorizontal: Spacing.three },
});
