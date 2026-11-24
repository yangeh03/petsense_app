import { DarkTheme, DefaultTheme, Stack, ThemeProvider, router } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { useColorScheme } from 'react-native';
import { useEffect } from 'react';

import { AnimatedSplashOverlay } from '@/components/animated-icon';
import { AuthProvider, useAuth } from '@/hooks/use-auth';
import { INBOX_FALLBACK_ROUTE, resolveInboxDeepLink } from '@/services/inbox-deep-link';
import {
  claimNavigation,
  clearLastNotificationResponse,
  installForegroundHandler,
  readLastNotificationRoute,
  releaseNavigation,
  rememberPendingInboxItem,
  restoreRegistration,
  startPermissionSync,
  subscribeNotificationResponses,
  takePendingInboxItem,
} from '@/services/push';

SplashScreen.preventAutoHideAsync();

/**
 * W13 通知响应处理：前台、后台、冷启动都走同一条路径。
 *
 * 载荷里只有稳定的收件箱 ID，定位交给 W12 的统一解析；解析失败时回退到伙伴页，
 * 不抛错也不重复创建对象（重复点击不会产生第二个对象，也不影响未读语义）。
 */
function useNotificationNavigation() {
  const { user, loading } = useAuth();
  const signedInUserId = user?.id ?? null;

  useEffect(() => {
    installForegroundHandler();
    return startPermissionSync();
  }, []);

  // 登录（含带会话的冷启动）后恢复本机推送绑定：退出登录会停用它，这里核对并重新注册
  useEffect(() => {
    if (loading || !signedInUserId) return;
    void restoreRegistration();
  }, [signedInUserId, loading]);

  useEffect(() => {
    if (loading) return; // 认证未就绪：先不处理冷启动响应，等登录状态确定
    const navigate = (route: string | null, inboxItemId: string | null) => {
      if (!route || !inboxItemId) return;
      if (!claimNavigation(inboxItemId)) return; // 重复点击折叠成一次导航
      if (!user) {
        // 未登录：先记下（连同账号），登录后续接
        void rememberPendingInboxItem(inboxItemId, null);
      }
      try {
        router.push(route as never);
      } catch {
        router.push(INBOX_FALLBACK_ROUTE as never);
      } finally {
        releaseNavigation();
      }
    };

    // 冷启动：App 由通知拉起
    const last = readLastNotificationRoute();
    if (last.inboxItemId) {
      if (!user) void rememberPendingInboxItem(last.inboxItemId, null);
      else navigate(last.route, last.inboxItemId);
      clearLastNotificationResponse();
    }

    return subscribeNotificationResponses(navigate);
  }, [user, loading]);

  // 登录续接：登录后把之前记下的收件箱 ID 取出来继续定位（只处理本账号的）
  useEffect(() => {
    if (loading || !user) return;
    let cancelled = false;
    void (async () => {
      const pendingId = await takePendingInboxItem(user.id);
      if (cancelled || !pendingId) return;
      if (!claimNavigation(pendingId)) return;
      const route = resolveInboxDeepLink(pendingId);
      if (!route) {
        releaseNavigation();
        return;
      }
      try {
        router.push(route as never);
      } catch {
        router.push(INBOX_FALLBACK_ROUTE as never);
      } finally {
        releaseNavigation();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user, loading]);
}

function RootNavigator() {
  useNotificationNavigation();
  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Screen name="(tabs)" />
      <Stack.Screen name="create" options={{ presentation: 'modal' }} />
      <Stack.Screen name="setup" options={{ presentation: 'modal', gestureEnabled: false }} />
      <Stack.Screen name="login" options={{ presentation: 'modal' }} />
      <Stack.Screen name="user" />
      <Stack.Screen name="post" />
      <Stack.Screen name="post-edit" options={{ presentation: 'modal' }} />
      <Stack.Screen name="community-search" />
      <Stack.Screen name="story-create" options={{ presentation: 'modal' }} />
      <Stack.Screen name="story" />
      <Stack.Screen name="nearby" />
      <Stack.Screen name="social-inbox" />
      <Stack.Screen name="bookmarks" />
      <Stack.Screen name="profile-edit" options={{ presentation: 'modal' }} />
      <Stack.Screen name="assistant-settings" options={{ presentation: 'modal' }} />
      <Stack.Screen name="notification-settings" options={{ presentation: 'modal' }} />
      <Stack.Screen
        name="records"
        options={{
          headerShown: true,
          title: '记录',
          headerBackButtonDisplayMode: 'minimal',
          headerBackTitle: '返回',
        }}
      />
      <Stack.Screen
        name="exercise-diary"
        options={{
          headerShown: true,
          title: '运动日记',
          headerBackButtonDisplayMode: 'minimal',
          headerBackTitle: '返回',
        }}
      />
      <Stack.Screen name="monitor" />
      <Stack.Screen
        name="agent-tasks"
        options={{
          headerShown: true,
          title: '委托簿',
          headerBackButtonDisplayMode: 'minimal',
          headerBackTitle: '返回',
        }}
      />
      <Stack.Screen name="pet" />
      <Stack.Screen name="collect" options={{ gestureEnabled: false }} />
      <Stack.Screen
        name="uwb-setup"
        options={{
          headerShown: true,
          title: '室内地图设置',
          headerBackButtonDisplayMode: 'minimal',
          headerBackTitle: '返回',
        }}
      />
      <Stack.Screen
        name="dog-map"
        options={{
          headerShown: true,
          title: '遛狗地图',
          headerBackButtonDisplayMode: 'minimal',
          headerBackTitle: '返回',
        }}
      />
    </Stack>
  );
}

export default function RootLayout() {
  const colorScheme = useColorScheme();
  return (
    <KeyboardProvider>
      <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
        <AuthProvider>
          <AnimatedSplashOverlay />
          <RootNavigator />
        </AuthProvider>
      </ThemeProvider>
    </KeyboardProvider>
  );
}
