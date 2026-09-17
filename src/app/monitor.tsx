import { SymbolView } from 'expo-symbols';
import { useState, type ComponentProps } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ConnectionDot } from '@/components/live/connection-dot';
import { LineChart } from '@/components/live/line-chart';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';
import type { SeriesKey, TelemetryStatus } from '@/types/telemetry';

type SymbolName = ComponentProps<typeof SymbolView>['name'];

const STATUS_LABEL: Record<TelemetryStatus, string> = {
  open: '已连接云端',
  connecting: '连接中…',
  reconnecting: '重连中…',
  closed: '未连接',
};

type MetricConfig = {
  key: SeriesKey;
  label: string;
  unit: string;
  decimals: number;
  icon: SymbolName;
};

const METRICS: MetricConfig[] = [
  {
    key: 'heartRate',
    label: '心率',
    unit: 'bpm',
    decimals: 0,
    icon: { ios: 'heart.fill', android: 'favorite', web: 'favorite' },
  },
  {
    key: 'spo2',
    label: '血氧',
    unit: '%',
    decimals: 0,
    icon: { ios: 'lungs.fill', android: 'air', web: 'air' },
  },
  {
    key: 'bodyTemp',
    label: '体温',
    unit: '℃',
    decimals: 1,
    icon: { ios: 'thermometer.medium', android: 'device_thermostat', web: 'thermometer' },
  },
];

/** 实时监测详情页：大图表 + 指标切换 + 设备信息。 */
export default function MonitorScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const { status, latest, series } = useTelemetry();
  const [activeKey, setActiveKey] = useState<SeriesKey>('heartRate');
  const active = METRICS.find((m) => m.key === activeKey) ?? METRICS[0];
  const points = series[active.key];
  const health = latest?.health?.valid ? latest.health : null;
  const battery = latest?.battery?.found ? latest.battery : null;
  const latestValue = points.length > 0 ? points[points.length - 1].v : null;

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.four }]}>
        {/* 顶部状态 */}
        <View style={styles.header}>
          <View style={styles.headerText}>
            <ThemedText type="subtitle">实时监测</ThemedText>
            <View style={styles.statusRow}>
              <ConnectionDot status={status} />
              <ThemedText type="small" themeColor="textSecondary">
                {STATUS_LABEL[status]} · {latest?.deviceId ?? 'petsense 设备'}
              </ThemedText>
            </View>
          </View>
          {battery && (
            <View style={styles.batteryBox}>
              <SymbolView
                name={{ ios: 'battery.75percent', android: 'battery_full', web: 'battery_full' }}
                size={18}
                tintColor={theme.tint}
              />
              <ThemedText type="smallBold">{Math.round(battery.percent)}%</ThemedText>
            </View>
          )}
        </View>

        {/* 指标切换 */}
        <View style={styles.segmentRow}>
          {METRICS.map((metric) => {
            const selected = metric.key === activeKey;
            return (
              <Pressable key={metric.key} onPress={() => setActiveKey(metric.key)}>
                <View style={[styles.segment, selected && { backgroundColor: theme.tint }]}>
                  <SymbolView
                    name={metric.icon}
                    size={14}
                    tintColor={selected ? '#ffffff' : theme.textSecondary}
                  />
                  <ThemedText
                    type="smallBold"
                    themeColor={selected ? undefined : 'textSecondary'}
                    style={selected && { color: '#ffffff' }}
                  >
                    {metric.label}
                  </ThemedText>
                </View>
              </Pressable>
            );
          })}
        </View>

        {/* 当前值 */}
        <View style={styles.valueRow}>
          <ThemedText style={styles.bigValue}>
            {latestValue === null ? '--' : latestValue.toFixed(active.decimals)}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary" style={styles.unit}>
            {active.unit}
          </ThemedText>
        </View>

        <ThemedView type="backgroundElement" style={styles.chartCard}>
          <LineChart
            points={points}
            color={theme.tint}
            unit={active.unit}
            decimals={active.decimals}
          />
        </ThemedView>

        {/* 次要健康指标 */}
        {health && (
          <View style={styles.infoGrid}>
            <InfoCell label="呼吸频率" value={`${health.respiration} 次/分`} />
            <InfoCell label="HRV (SDNN)" value={`${health.hrvSdnn} ms`} />
            <InfoCell label="微循环" value={`${health.microCir}`} />
            <InfoCell label="疲劳度" value={`${health.fatigue}`} />
          </View>
        )}

        <ThemedText type="small" themeColor="textSecondary" style={styles.footer}>
          数据经 MQTT → 云端 → WebSocket 实时推送{'\n'}支持跨网连接，设备与手机无需同一局域网
        </ThemedText>
      </ThemedView>
    </ScrollView>
  );
}

function InfoCell({ label, value }: { label: string; value: string }) {
  return (
    <ThemedView type="backgroundElement" style={infoStyles.cell}>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
      <ThemedText type="smallBold">{value}</ThemedText>
    </ThemedView>
  );
}

const infoStyles = StyleSheet.create({
  cell: {
    flex: 1,
    borderRadius: 14,
    padding: Spacing.three,
    gap: Spacing.one,
  },
});

const styles = StyleSheet.create({
  scrollView: {
    flex: 1,
  },
  contentContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
  },
  container: {
    flex: 1,
    maxWidth: 640,
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.six,
    gap: Spacing.four,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  headerText: {
    gap: Spacing.one,
    flex: 1,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  batteryBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.one,
    backgroundColor: 'rgba(128,128,128,0.1)',
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two,
    borderRadius: 12,
  },
  segmentRow: {
    flexDirection: 'row',
    gap: Spacing.two,
  },
  segment: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.one,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two,
    borderRadius: 20,
    backgroundColor: 'rgba(128,128,128,0.12)',
  },
  valueRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: Spacing.two,
  },
  bigValue: {
    fontSize: 56,
    fontWeight: '700',
    lineHeight: 60,
    fontVariant: ['tabular-nums'],
  },
  unit: {
    fontSize: 16,
  },
  chartCard: {
    borderRadius: 20,
    padding: Spacing.three,
    alignItems: 'center',
  },
  infoGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.two,
  },
  footer: {
    textAlign: 'center',
    lineHeight: 18,
  },
});
