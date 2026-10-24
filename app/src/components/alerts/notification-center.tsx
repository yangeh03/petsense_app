import { SymbolView } from 'expo-symbols';
import { useEffect, useState, type ComponentProps } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import Animated, { FadeInDown, FadeOut } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { AlertEvent, AlertRuleId, AlertSeverity } from '@/types/alerts';

type SymbolName = ComponentProps<typeof SymbolView>['name'];

const RULE_ICON: Record<AlertRuleId, SymbolName> = {
  heartRateHigh: { ios: 'heart.fill', android: 'favorite', web: 'favorite' },
  heartRateLow: { ios: 'heart.fill', android: 'favorite', web: 'favorite' },
  bodyTempHigh: {
    ios: 'thermometer.high',
    android: 'device_thermostat',
    web: 'device_thermostat',
  },
  bodyTempLow: {
    ios: 'thermometer.low',
    android: 'device_thermostat',
    web: 'device_thermostat',
  },
  spo2Low: { ios: 'lungs.fill', android: 'air', web: 'air' },
  sensorOff: { ios: 'exclamationmark.triangle.fill', android: 'warning', web: 'warning' },
  batteryLow: { ios: 'battery.25percent', android: 'battery_alert', web: 'battery_alert' },
  dataStale: { ios: 'wifi.slash', android: 'wifi_off', web: 'wifi_off' },
};

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  critical: '需要关注',
  warning: '留意',
  info: '提示',
};

/** 通知中心抽屉：活跃告警按严重级排列，点击折叠（回稳后自动消失），不打断任何页面。 */
export function NotificationCenter({
  visible,
  alerts,
  onClose,
  onAcknowledge,
}: {
  visible: boolean;
  alerts: AlertEvent[];
  onClose: () => void;
  onAcknowledge: (ruleId: AlertRuleId) => void;
}) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!visible) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [visible]);

  const severityColor = (severity: AlertSeverity) =>
    severity === 'critical' ? theme.danger : severity === 'warning' ? theme.warning : theme.info;

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
              通知
            </ThemedText>
            <Pressable onPress={onClose} hitSlop={10} accessibilityLabel="关闭通知中心">
              <SymbolView
                name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
                size={26}
                tintColor={theme.textSecondary}
              />
            </Pressable>
          </View>

          {alerts.length === 0 ? (
            <View style={styles.empty}>
              <Text style={styles.emptyEmoji}>🐾</Text>
              <ThemedText type="small" themeColor="textSecondary">
                一切正常，暂没有需要关注的事
              </ThemedText>
            </View>
          ) : (
            <ScrollView style={styles.list} contentContainerStyle={{ gap: Spacing.two }}>
              {alerts.map((alert) => {
                const color = severityColor(alert.severity);
                return (
                  <Animated.View
                    key={alert.ruleId}
                    entering={FadeInDown.duration(180)}
                    exiting={FadeOut.duration(120)}
                  >
                    <Pressable
                      onPress={() => onAcknowledge(alert.ruleId)}
                      style={[styles.row, { backgroundColor: theme.backgroundElement }]}
                    >
                      <View style={[styles.iconDot, { backgroundColor: `${color}1F` }]}>
                        <SymbolView name={RULE_ICON[alert.ruleId]} size={17} tintColor={color} />
                      </View>
                      <View style={styles.texts}>
                        <ThemedText type="smallBold">
                          {alert.label}
                          {alert.detail ? ` · ${alert.detail}` : ''}
                        </ThemedText>
                        <ThemedText type="small" themeColor="textSecondary">
                          {SEVERITY_LABEL[alert.severity]} · 已持续{' '}
                          {formatDuration(now - alert.triggeredAt)}
                          {alert.acknowledged ? ' · 已折叠' : ' · 点击折叠'}
                        </ThemedText>
                      </View>
                      {!alert.acknowledged && (
                        <View style={[styles.dot, { backgroundColor: color }]} />
                      )}
                    </Pressable>
                  </Animated.View>
                );
              })}
            </ScrollView>
          )}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function formatDuration(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
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
    maxHeight: '72%',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: Spacing.three,
  },
  title: {
    fontSize: 22,
  },
  empty: {
    alignItems: 'center',
    gap: Spacing.two,
    paddingVertical: Spacing.six,
  },
  emptyEmoji: {
    fontSize: 34,
  },
  list: {
    flexGrow: 0,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    borderRadius: 16,
    padding: Spacing.three,
  },
  iconDot: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
  },
  texts: {
    flex: 1,
    gap: 2,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
});
