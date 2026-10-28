import AsyncStorage from '@react-native-async-storage/async-storage';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { searchCommunity } from '@/services/api';
import type { FollowListUser, Post } from '@/types/social';

type Kind = 'all' | 'posts' | 'users' | 'places';
type Place = { name: string; postsCount: number };
type Result = { posts: Post[]; users: FollowListUser[]; places: Place[] };
const SEARCH_HISTORY = 'petsense.community.recentSearches';

export default function CommunitySearchScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<Kind>('all');
  const [selectedPlace, setSelectedPlace] = useState<string | null>(null);
  const [result, setResult] = useState<Result>({ posts: [], users: [], places: [] });
  const [recent, setRecent] = useState<string[]>([]);
  const [historyLoadedFor, setHistoryLoadedFor] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [loadedKey, setLoadedKey] = useState('');
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const keyword = query.trim();
  const historyKey = `${SEARCH_HISTORY}.${user?.id ?? 'guest'}`;
  const visibleRecent = historyLoadedFor === historyKey ? recent : [];
  const searchKey = `${user?.id ?? 0}:${kind}:${keyword}:${selectedPlace ?? ''}`;
  const activeSearchKey = useRef(searchKey);

  useEffect(() => {
    activeSearchKey.current = searchKey;
  }, [searchKey]);

  useEffect(() => {
    let active = true;
    void AsyncStorage.getItem(historyKey)
      .then((value) => {
        if (active) {
          setRecent(value ? (JSON.parse(value) as string[]) : []);
          setHistoryLoadedFor(historyKey);
        }
      })
      .catch(() => {
        if (active) {
          setRecent([]);
          setHistoryLoadedFor(historyKey);
        }
      });
    return () => {
      active = false;
    };
  }, [historyKey]);

  useEffect(() => {
    if (keyword.length < 2) {
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      void searchCommunity(keyword, kind, controller.signal, undefined, selectedPlace ?? undefined)
        .then((data) => {
          setResult(data);
          setNextCursor(data.page.nextCursor);
          setError('');
          setLoadedKey(searchKey);
        })
        .catch((exc) => {
          if (!controller.signal.aborted) {
            setError(exc instanceof Error ? exc.message : '搜索失败，请重试');
            setLoadedKey(searchKey);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [keyword, kind, searchKey, selectedPlace]);

  const saveSearch = (value: string) => {
    const next = [value, ...visibleRecent.filter((item) => item !== value)].slice(0, 8);
    setRecent(next);
    void AsyncStorage.setItem(historyKey, JSON.stringify(next));
  };

  const loadMore = async () => {
    if (!nextCursor || loadingMore || kind === 'all') return;
    setLoadingMore(true);
    try {
      const data = await searchCommunity(
        keyword,
        kind,
        undefined,
        nextCursor,
        selectedPlace ?? undefined,
      );
      if (activeSearchKey.current !== searchKey) return;
      setResult((current) => ({
        posts: [...current.posts, ...data.posts],
        users: [...current.users, ...data.users],
        places: [...current.places, ...data.places],
      }));
      setNextCursor(data.page.nextCursor);
      setError('');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '更多结果加载失败');
    } finally {
      setLoadingMore(false);
    }
  };

  const rows: (
    | { type: 'post'; value: Post }
    | { type: 'user'; value: FollowListUser }
    | { type: 'place'; value: Place }
  )[] = [
    ...result.posts.map((value) => ({ type: 'post' as const, value })),
    ...result.users.map((value) => ({ type: 'user' as const, value })),
    ...result.places.map((value) => ({ type: 'place' as const, value })),
  ];

  return (
    <ThemedView style={[styles.screen, { paddingTop: insets.top + Spacing.two }]}>
      <View style={styles.content}>
        <View style={[styles.searchRow, { borderColor: theme.backgroundSelected }]}>
          <SymbolView
            name={{ ios: 'magnifyingglass', android: 'search', web: 'search' }}
            size={20}
            tintColor={theme.textSecondary}
          />
          <TextInput
            value={query}
            onChangeText={(value) => {
              setQuery(value);
              setSelectedPlace(null);
            }}
            autoFocus
            placeholder="搜索帖子、宠友、地点"
            placeholderTextColor={theme.textSecondary}
            style={[styles.input, { color: theme.text }]}
            returnKeyType="search"
            onSubmitEditing={() => {
              if (keyword.length >= 2) saveSearch(keyword);
            }}
          />
          {!!query && (
            <Pressable
              onPress={() => {
                setQuery('');
                setSelectedPlace(null);
              }}
              accessibilityLabel="清除搜索词"
            >
              <SymbolView
                name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
                size={20}
                tintColor={theme.textSecondary}
              />
            </Pressable>
          )}
          <Pressable onPress={() => router.back()} accessibilityLabel="取消搜索">
            <ThemedText type="small" style={{ color: theme.tint }}>
              取消
            </ThemedText>
          </Pressable>
        </View>
        <View style={styles.categories}>
          {(['all', 'posts', 'users', 'places'] as const).map((item) => (
            <Pressable
              key={item}
              onPress={() => {
                setKind(item);
                setSelectedPlace(null);
              }}
              style={[
                styles.category,
                { backgroundColor: item === kind ? theme.tint : theme.backgroundSelected },
              ]}
            >
              <ThemedText
                type="small"
                style={{ color: item === kind ? '#ffffff' : theme.textSecondary }}
              >
                {{ all: '综合', posts: '帖子', users: '宠友', places: '地点' }[item]}
              </ThemedText>
            </Pressable>
          ))}
        </View>
        {keyword.length < 2 ? (
          <View style={styles.recent}>
            <ThemedText type="small" themeColor="textSecondary">
              {keyword ? '再输入一个字开始搜索' : '最近搜索'}
            </ThemedText>
            {!keyword &&
              visibleRecent.map((item) => (
                <View key={item} style={styles.recentRow}>
                  <Pressable onPress={() => setQuery(item)} style={styles.recentItem}>
                    <ThemedText type="small">{item}</ThemedText>
                  </Pressable>
                  <Pressable
                    onPress={() => {
                      const next = visibleRecent.filter((entry) => entry !== item);
                      setRecent(next);
                      void AsyncStorage.setItem(historyKey, JSON.stringify(next));
                    }}
                    accessibilityLabel={`删除最近搜索 ${item}`}
                  >
                    <ThemedText type="small" style={{ color: theme.textSecondary }}>
                      删除
                    </ThemedText>
                  </Pressable>
                </View>
              ))}
          </View>
        ) : loading || loadedKey !== searchKey ? (
          <ActivityIndicator color={theme.tint} />
        ) : error ? (
          <ThemedText type="small" style={{ color: theme.danger }}>
            {error}
          </ThemedText>
        ) : rows.length === 0 ? (
          <ThemedText type="small" themeColor="textSecondary">
            没有找到相关内容
          </ThemedText>
        ) : (
          <FlatList
            data={rows}
            ListFooterComponent={
              nextCursor ? (
                <Pressable onPress={() => void loadMore()}>
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    {loadingMore ? '加载中…' : '加载更多结果'}
                  </ThemedText>
                </Pressable>
              ) : undefined
            }
            keyExtractor={(item) =>
              `${item.type}:${
                item.type === 'post'
                  ? item.value.id
                  : item.type === 'user'
                    ? item.value.id
                    : item.value.name
              }`
            }
            renderItem={({ item }) => {
              if (item.type === 'post')
                return (
                  <Pressable
                    onPress={() => {
                      saveSearch(keyword);
                      router.push(`/post?id=${item.value.id}`);
                    }}
                    style={[styles.row, { borderColor: theme.backgroundSelected }]}
                    accessibilityRole="button"
                    accessibilityLabel={`${item.value.display_name} 的帖子：${item.value.caption || '图片动态'}`}
                  >
                    <ThemedText type="smallBold">{item.value.display_name} · 帖子</ThemedText>
                    <ThemedText type="small" numberOfLines={2}>
                      {item.value.caption || '图片动态'}
                    </ThemedText>
                  </Pressable>
                );
              if (item.type === 'user')
                return (
                  <Pressable
                    onPress={() => {
                      saveSearch(keyword);
                      router.push(`/user?id=${item.value.id}`);
                    }}
                    style={[styles.row, { borderColor: theme.backgroundSelected }]}
                    accessibilityRole="button"
                    accessibilityLabel={`查看 ${item.value.display_name} 的主页`}
                  >
                    <ThemedText type="smallBold">
                      {item.value.avatar_emoji} {item.value.display_name}
                    </ThemedText>
                  </Pressable>
                );
              return (
                <Pressable
                  onPress={() => {
                    saveSearch(keyword);
                    setQuery(item.value.name);
                    setKind('posts');
                    setSelectedPlace(item.value.name);
                  }}
                  style={[styles.row, { borderColor: theme.backgroundSelected }]}
                  accessibilityRole="button"
                  accessibilityLabel={`地点 ${item.value.name}，${item.value.postsCount} 条相关帖子`}
                >
                  <View style={styles.placeTitle}>
                    <SymbolView
                      name={{ ios: 'mappin.and.ellipse', android: 'place', web: 'place' }}
                      size={16}
                      tintColor={theme.tint}
                    />
                    <ThemedText type="smallBold">{item.value.name}</ThemedText>
                  </View>
                  <ThemedText type="small" themeColor="textSecondary">
                    {item.value.postsCount} 条相关帖子
                  </ThemedText>
                </Pressable>
              );
            }}
          />
        )}
      </View>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: {
    maxWidth: MaxContentWidth,
    width: '100%',
    alignSelf: 'center',
    flex: 1,
    paddingHorizontal: Spacing.four,
    gap: Spacing.three,
  },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderWidth: 1,
    borderRadius: 16,
    padding: Spacing.two,
  },
  input: { flex: 1, fontSize: 15, paddingVertical: Spacing.two },
  categories: { flexDirection: 'row', gap: Spacing.two },
  category: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two, borderRadius: 16 },
  recent: { gap: Spacing.three },
  recentRow: { flexDirection: 'row', justifyContent: 'space-between' },
  recentItem: { flex: 1 },
  row: { borderBottomWidth: 1, paddingVertical: Spacing.three, gap: Spacing.one },
  placeTitle: { flexDirection: 'row', alignItems: 'center', gap: Spacing.one },
});
