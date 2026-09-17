import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, View } from 'react-native';

import { ConnectionDot } from '@/components/live/connection-dot';
import { Sparkline } from '@/components/live/sparkline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';
import type { TelemetryStatus } from '@/types/telemetry';

const STATUS_LABEL: Record<TelemetryStatus, string> = {
  open: '在线 · 实时',
  connecting: '连接中…',
  reconnecting: '重连中…',
  closed: '未连接',
};

/** 首页「实时监测」卡片：云端 WebSocket 实时数据（心率 / 血氧 / 体温 / 电量）。 */
export function LiveMonitorCard() {
  const router = useRouter();
  const theme = useTheme();
  const { status, latest, series } = useTelemetry();
  const health = latest?.health?.valid ? latest.health : null;
  const battery = latest?.battery?.found ? latest.battery : null;

  return (
    <Pressable
      onPress={() => router.push('/monitor')}
      style={({ pressed }) => pressed && styles.pressed}
    >
      <ThemedView type="backgroundElement" style={styles.card}>
        <View style={styles.header}>
          <ConnectionDot status={status} />
          <ThemedText type="smallBold" style={styles.title}>
            实时监测
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {STATUS_LABEL[status]}
          </ThemedText>
          <SymbolView
            name={{ ios: 'chevron.right', android: 'chevron_right', web: 'chevron_right' }}
            size={13}
            tintColor={theme.textSecondary}
          />
        </View>

        {health ? (
          <>
            <View style={styles.mainRow}>
              <View style={styles.heartBlock}>
                <ThemedText type="small" themeColor="textSecondary">
                  心率
                </ThemedText>
                <View style={styles.heartValueRow}>
                  <ThemedText style={styles.heartValue}>{Math.round(health.heartRate)}</ThemedText>
                  <ThemedText type="small" themeColor="textSecondary">
                    bpm
                  </ThemedText>
                </View>
              </View>
              <Sparkline points={series.heartRate} color={theme.tint} width={140} height={52} />
            </View>
            <View style={styles.metricRow}>
              <MetricPill
                label="血氧"
                value={health.spo2 > 0 ? `${Math.round(health.spo2)}%` : '--'}
              />
              <MetricPill
                label="体温"
                value={health.bodyTemp > 0 ? `${health.bodyTemp.toFixed(1)}℃` : '--'}
              />
              <MetricPill
                label="设备电量"
                value={battery ? `${Math.round(battery.percent)}%` : '--'}
              />
            </View>
          </>
        ) : (
          <View style={styles.waiting}>
            <ThemedText type="small" themeColor="textSecondary">
              {status === 'open'
                ? '等待设备上传健康数据…'
                : '设备离线时展示最近数据，联网后自动恢复'}
            </ThemedText>
          </View>
        )}
      </ThemedView>
    </Pressable>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <View style={pillStyles.pill}>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
      <ThemedText type="smallBold">{value}</ThemedText>
    </View>
  );
}

const pillStyles = StyleSheet.create({
  pill: {
    flex: 1,
    borderRadius: 12,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two,
    gap: 2,
  },
});

const styles = StyleSheet.create({
  card: {
    borderRadius: 20,
    padding: Spacing.four,
    gap: Spacing.three,
  },
  pressed: {
    opacity: 0.9,
    transform: [{ scale: 0.99 }],
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  title: {
    marginRight: 'auto',
  },
  mainRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  heartBlock: {
    gap: Spacing.one,
  },
  heartValueRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: Spacing.one,
  },
  heartValue: {
    fontSize: 44,
    fontWeight: '700',
    lineHeight: 48,
    fontVariant: ['tabular-nums'],
  },
  metricRow: {
    flexDirection: 'row',
    gap: Spacing.two,
    backgroundColor: 'rgba(128,128,128,0.08)',
    borderRadius: 12,
  },
  waiting: {
    alignItems: 'center',
    paddingVertical: Spacing.three,
  },
});
