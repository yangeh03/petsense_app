/**
 * W12 深链解析页（`petsenseapp://inbox/<id>` → 应用内路由 `/inbox/<id>`）。
 *
 * 登录后由服务端解析消息类型与目标，再跳到稳定路由；**目标成功显示后**才标已读。
 * 404、他人 ID、离线与目标加载失败都不标已读（明确状态文案，见 spec W12-A3/A4）。
 */

import { useLocalSearchParams, useRouter, type Href } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ApiError } from '@/services/api';
import { Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { resolveInboxItem } from '@/services/agent-inbox';
type State = 'loading' | 'unavailable' | 'missing' | 'error';

export default function InboxDeepLinkScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const params = useLocalSearchParams<{ id?: string | string[] }>();
  const rawId = Array.isArray(params.id) ? params.id[0] : params.id;
  const itemId = Number(rawId);
  const invalid = !Number.isFinite(itemId) || itemId <= 0;
  const [state, setState] = useState<State>('loading');
  const [message, setMessage] = useState('');
  const handled = useRef(false);
  const ownerRef = useRef(user?.id ?? null);
  useEffect(() => {
    ownerRef.current = user?.id ?? null;
  }, [user?.id]);

  useEffect(() => {
    if (handled.current || authLoading || !user || invalid) return;
    handled.current = true;
    const requestOwner = user.id;
    (async () => {
      try {
        const data = await resolveInboxItem(itemId);
        // 账号已变更：丢弃这次响应（不带着上一个账号的来源跳转）
        if (ownerRef.current !== requestOwner) return;
        const target = data.target;
        if (!target.available) {
          setState('unavailable');
          setMessage('这条通知的来源已不可用');
          return;
        }
        const query = Object.entries(target.params)
          .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
          .join('&');
        // 只跳转，不在此处标已读：目标在伙伴页**定位并展示**那条来源后才标（W12-A3）
        router.replace(`${target.route}${query ? `?${query}` : ''}` as Href);
      } catch (exc) {
        if (ownerRef.current !== requestOwner) return;
        // 404：条目不存在或不属于当前账号——不会「稍后」变好，如实说明
        if (exc instanceof ApiError && exc.status === 404) {
          setState('missing');
          setMessage('这条通知不存在，或不属于当前账号');
          return;
        }
        setState('error');
        setMessage('暂时打不开这条通知，请稍后再试');
      }
    })();
  }, [authLoading, user, invalid, itemId, router]);

  if (authLoading || (state === 'loading' && user && !invalid)) {
    return (
      <View style={[styles.screen, { backgroundColor: theme.background }]}>
        <ActivityIndicator color={theme.tint} />
      </View>
    );
  }

  const loggedOut = !user;
  const title = loggedOut
    ? '请先登录'
    : invalid
      ? '链接无效'
      : state === 'unavailable'
        ? '来源已不可用'
        : state === 'missing'
          ? '找不到这条通知'
          : '打不开';

  return (
    <View style={[styles.screen, { backgroundColor: theme.background }]}>
      <ThemedText type="subtitle">{title}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {loggedOut
          ? '登录后会自动回到这条通知'
          : user && !invalid && state === 'loading'
            ? '正在打开…'
            : message || '可以先回到收件箱查看'}
      </ThemedText>
      {loggedOut ? (
        <Pressable
          onPress={() => router.push('/login')}
          style={[styles.action, { backgroundColor: theme.tintBackground }]}
        >
          <Text style={[styles.actionText, { color: theme.tintForeground }]}>登录 / 注册</Text>
        </Pressable>
      ) : (
        <Pressable
          onPress={() => router.replace('/agent-inbox')}
          style={[styles.action, { backgroundColor: theme.tintBackground }]}
        >
          <Text style={[styles.actionText, { color: theme.tintForeground }]}>去收件箱</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.three,
    padding: Spacing.four,
  },
  action: {
    borderRadius: 999,
    paddingHorizontal: Spacing.four,
    paddingVertical: Spacing.two,
  },
  actionText: { fontSize: 14, fontWeight: '600' },
});
