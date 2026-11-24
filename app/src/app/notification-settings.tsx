/**
 * W13 推送设置页（iOS 首版，modal）。
 *
 * 入口：我的页「设置」分组里的「通知推送」行。
 *
 * 行为：
 * - 本轮仅 iOS 支持原生推送。Android 与 Web **不注册、不申请系统权限**，本页只说明
 *   现状并保留站内收件箱入口（spec §客户端与发布）。
 * - 用户在设置里主动开启时才申请系统权限（D3）：应用启动与前台都不弹权限框。
 * - 状态覆盖：已开启 / 系统拒绝 / 系统撤回 / 暂不可用 / 待重试，每种都有明确文案。
 * - 锁屏只显示通用提示，因此本页不展示任何通知内容预览。
 */

import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
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
  delivered: '已送达',
  failed: '发送失败',
  rejected: '设备已失效',
  unknown: '结果未知（不重试）',
  suppressed: '按规则未推送',
};

export default function PushSettingsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { user } = useAuth();
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [deliveries, setDeliveries] = useState<PushDeliveryRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    void (async () => {
      const next = await readPushStatus();
      const records = await readDeliveries();
      if (cancelled) return;
      setStatus(next);
      setDeliveries(records);
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  const back = () => (router.canGoBack() ? router.back() : router.dismiss());

  const toggle = async (enabled: boolean) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const next = enabled ? await enablePush() : await disablePush();
      setStatus(next);
      setDeliveries(await readDeliveries());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '操作失败，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  const supported = status?.supported ?? true;
  const enabled = status?.enabled ?? false;

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

      <ScrollView contentContainerStyle={styles.content}>
        {!user ? (
          <ThemedText type="small" themeColor="textSecondary">
            登录后可开启通知推送。
          </ThemedText>
        ) : (
          <>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
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
                  <Pressable
                    onPress={() => void toggle(!enabled)}
                    disabled={!supported}
                    accessibilityRole="switch"
                    accessibilityState={{ checked: enabled, disabled: !supported }}
                    accessibilityLabel="推送提醒开关"
                    style={[
                      styles.switchTrack,
                      {
                        backgroundColor: enabled ? theme.tint : theme.backgroundSelected,
                        opacity: supported ? 1 : 0.5,
                      },
                    ]}
                  >
                    <View
                      style={[
                        styles.switchThumb,
                        {
                          backgroundColor: theme.backgroundElement,
                          alignSelf: enabled ? 'flex-end' : 'flex-start',
                        },
                      ]}
                    />
                  </Pressable>
                )}
              </View>
              {!!error && (
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {error}
                </ThemedText>
              )}
            </View>

            {supported && (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <ThemedText type="smallBold">会收到什么</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  关键设备告警、明确委托的到期回信，以及高相关的个人变化。锁屏只显示通用提示，不含宠物信息。
                </ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  安静档下不推送个人变化；其余内容仍然留在 App 里，站内收件箱一直可用。
                </ThemedText>
              </View>
            )}

            {!supported && (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <ThemedText type="smallBold">当前平台</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  本轮只在 iOS 交付原生推送。Android 与 Web
                  不会注册推送、也不会申请通知权限，收件箱照常使用。
                </ThemedText>
              </View>
            )}

            {supported && deliveries.length > 0 && (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <ThemedText type="smallBold">最近的推送</ThemedText>
                {deliveries.slice(0, 8).map((item) => (
                  <View key={item.id} style={styles.deliveryRow}>
                    <ThemedText type="small">
                      {DELIVERY_STATE_TEXT[item.state] ?? item.state}
                    </ThemedText>
                    <ThemedText type="small" themeColor="textSecondary">
                      {item.attempts} 次尝试{item.errorCode ? ` · ${item.errorCode}` : ''}
                    </ThemedText>
                  </View>
                ))}
                <ThemedText type="small" themeColor="textSecondary">
                  推送失败不会影响站内消息，任何一条都能在收件箱里打开。
                </ThemedText>
              </View>
            )}
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
  switchTrack: {
    width: 52,
    height: 32,
    borderRadius: 16,
    padding: 3,
    justifyContent: 'center',
    minHeight: 48,
  },
  switchThumb: { width: 26, height: 26, borderRadius: 13 },
  deliveryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
});
