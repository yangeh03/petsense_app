import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  AppState,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  View,
} from 'react-native';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { resolveInboxDeepLink } from '@/services/inbox-deep-link';
import {
  disablePush,
  enablePush,
  readDeliveries,
  readPushStatus,
  type PushDeliveryRecord,
  type PushStatus,
} from '@/services/push';

const DELIVERY_STATE_TEXT: Record<string, string> = {
  pending: '待发送',
  sending: '发送中',
  sent: '已提交推送服务',
  delivered: '已交给系统通知服务',
  failed: '发送失败',
  rejected: '设备已失效',
  unknown: '结果暂无法确认',
  suppressed: '本次未推送',
};

type Snapshot = { owner: number; status: PushStatus; deliveries: PushDeliveryRecord[] };

export default function PushSettingsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const ownerId = user?.id ?? null;
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [operation, setOperation] = useState<{
    owner: number;
    error: string;
    busy: boolean;
  } | null>(null);
  const generation = useRef(0);
  // 账号标签保证退出、换账号的首帧就隐藏旧数据，不等待 effect 或轮询。
  const own = ownerId != null && snapshot?.owner === ownerId ? snapshot : null;
  const status = own?.status ?? null;
  const deliveries = own?.deliveries ?? [];
  const busy = operation?.owner === ownerId && operation.busy;
  const error = operation?.owner === ownerId ? operation.error : '';

  useEffect(() => {
    if (ownerId == null) return;
    const owner = ownerId;
    let active = true;
    const refresh = async () => {
      const version = generation.current;
      const [next, records] = await Promise.all([readPushStatus(), readDeliveries()]);
      if (active && version === generation.current)
        setSnapshot({ owner, status: next, deliveries: records });
    };
    void refresh();
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') void refresh();
    });
    return () => {
      active = false;
      generation.current += 1;
      subscription.remove();
    };
  }, [ownerId]);

  const back = () => (router.canGoBack() ? router.back() : router.replace('/profile'));
  const toggle = async (enabled: boolean) => {
    if (busy || ownerId == null) return;
    const owner = ownerId;
    const version = ++generation.current;
    setOperation({ owner, busy: true, error: '' });
    try {
      const next = enabled ? await enablePush() : await disablePush();
      const records = await readDeliveries();
      if (version === generation.current) {
        setSnapshot({ owner, status: next, deliveries: records });
        setOperation({ owner, busy: false, error: '' });
      }
    } catch {
      if (version === generation.current)
        setOperation({ owner, busy: false, error: '未能更新通知设置，请重试。' });
    }
  };
  const openSystemSettings = () => {
    void Linking.openSettings().catch(() => {
      if (ownerId != null)
        setOperation({ owner: ownerId, busy: false, error: '请在系统设置中打开 PetSense 通知。' });
    });
  };
  const supported = status?.supported ?? true;
  const cardStyle = [
    styles.card,
    { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
  ];

  return (
    <View style={[styles.screen, { backgroundColor: theme.background }]}>
      <View style={styles.header}>
        <Pressable
          onPress={back}
          accessibilityRole="button"
          accessibilityLabel="返回"
          style={styles.headerAction}
        >
          <SymbolView
            name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
            size={22}
            tintColor={theme.text}
          />
        </Pressable>
        <ThemedText type="smallBold" style={styles.title}>
          通知推送
        </ThemedText>
        <View style={styles.headerAction} />
      </View>
      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + Spacing.four }]}
      >
        {!user ? (
          <ThemedText type="small" themeColor="textSecondary">
            登录后可开启通知推送。
          </ThemedText>
        ) : (
          <>
            <View style={cardStyle}>
              <View style={styles.row}>
                <View style={styles.rowText}>
                  <ThemedText type="smallBold">推送提醒</ThemedText>
                  <ThemedText type="small" themeColor="textSecondary">
                    {status?.message ?? '正在读取推送状态…'}
                  </ThemedText>
                </View>
                {busy ? (
                  <ActivityIndicator color={theme.tint} />
                ) : (
                  <Switch
                    value={status?.enabled ?? false}
                    onValueChange={(value) => void toggle(value)}
                    disabled={!status || !supported}
                    accessibilityLabel="推送提醒开关"
                    trackColor={{ false: theme.backgroundSelected, true: theme.tint }}
                    ios_backgroundColor={theme.backgroundSelected}
                  />
                )}
              </View>
              {(status?.permission === 'denied' ||
                status?.permission === 'revoked' ||
                status?.permission === 'granted') && (
                <Pressable
                  onPress={openSystemSettings}
                  accessibilityRole="button"
                  style={styles.linkAction}
                >
                  <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                    前往系统设置
                  </ThemedText>
                </Pressable>
              )}
              {status?.registration === 'error' && (
                <Pressable
                  onPress={() => void toggle(true)}
                  disabled={busy}
                  accessibilityRole="button"
                  style={styles.linkAction}
                >
                  <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                    重新尝试
                  </ThemedText>
                </Pressable>
              )}
              {!!error && (
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {error}
                </ThemedText>
              )}
            </View>
            <View style={cardStyle}>
              <ThemedText type="smallBold">{supported ? '会收到什么' : '站内消息照常'}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {supported
                  ? '关键设备告警、明确委托的到期回信，以及高相关的个人变化。锁屏只显示通用提示，不含宠物信息。'
                  : '当前仅 iOS 支持系统推送。在这里，你仍可通过收件箱查看提醒与回信。'}
              </ThemedText>
              {supported && (
                <ThemedText type="small" themeColor="textSecondary">
                  安静档下不推送个人变化；设备告警和委托回信仍按通知设置发送。
                </ThemedText>
              )}
            </View>
            {supported && deliveries.length > 0 && (
              <View style={cardStyle}>
                <ThemedText type="smallBold">最近的推送</ThemedText>
                {deliveries.slice(0, 8).map((item) => (
                  <Pressable
                    key={item.id}
                    style={styles.deliveryRow}
                    accessibilityRole="button"
                    accessibilityLabel={`${DELIVERY_STATE_TEXT[item.state] ?? '状态待确认'}，查看站内消息`}
                    onPress={() => {
                      const route = resolveInboxDeepLink(item.inboxItemId);
                      if (route) router.push(route as never);
                    }}
                  >
                    <View style={styles.rowText}>
                      <ThemedText type="small">
                        {DELIVERY_STATE_TEXT[item.state] ?? '状态待确认'}
                      </ThemedText>
                      <ThemedText type="small" themeColor="textSecondary">
                        {new Date(item.createdAt).toLocaleString('zh-CN', {
                          month: 'numeric',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </ThemedText>
                    </View>
                    <SymbolView
                      name={{
                        ios: 'chevron.right',
                        android: 'chevron_right',
                        web: 'chevron_right',
                      }}
                      size={16}
                      tintColor={theme.textSecondary}
                    />
                  </Pressable>
                ))}
                <ThemedText type="small" themeColor="textSecondary">
                  推送状态不代表已在设备上展示。点开可查看站内消息。
                </ThemedText>
              </View>
            )}
            <Pressable
              accessibilityRole="button"
              onPress={() => router.push('/agent-inbox')}
              style={styles.linkAction}
            >
              <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                查看站内收件箱
              </ThemedText>
            </Pressable>
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  header: {
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    padding: Spacing.three,
    paddingBottom: Spacing.two,
  },
  headerAction: { minWidth: 52, minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  title: { flex: 1, textAlign: 'center', fontSize: 18 },
  content: {
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
    padding: Spacing.four,
    gap: Spacing.three,
  },
  card: { padding: Spacing.four, borderRadius: 24, gap: Spacing.three },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  rowText: { flex: 1, gap: Spacing.one },
  linkAction: { minHeight: 44, justifyContent: 'center', alignItems: 'center' },
  deliveryRow: {
    minHeight: 48,
    paddingVertical: Spacing.two,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
});
