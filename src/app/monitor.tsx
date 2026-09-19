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
  const { status, latest, series, isFresh } = useTelemetry();
  const [activeKey, setActiveKey] = useState<SeriesKey>('heartRate');
  const active = METRICS.find((m) => m.key === activeKey) ?? METRICS[0];
  const points = series[active.key];
  const health = latest?.health?.valid ? latest.health : null;
  const battery = latest?.battery?.found ? latest.battery : null;
  const latestValue = health && health[active.key] > 0 ? health[active.key] : null;
  const imu = latest?.imu?.ok ? latest.imu : null;
  const audio = latest?.audio?.ok ? latest.audio : null;
  const experiment = latest?.experiment;

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
              <ConnectionDot status={status === 'open' && !isFresh ? 'closed' : status} />
              <ThemedText type="small" themeColor="textSecondary">
                {status === 'open' && !isFresh ? '云端已连接 · 等待设备' : STATUS_LABEL[status]} ·{' '}
                {latest?.deviceId ?? 'petsense 设备'}
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
            <InfoCell
              label="呼吸频率"
              value={health.respiration > 0 ? `${health.respiration} 次/分` : '--'}
            />
            <InfoCell
              label="HRV (SDNN)"
              value={health.hrvSdnn > 0 ? `${health.hrvSdnn} ms` : '--'}
            />
            <InfoCell label="微循环" value={health.microCir > 0 ? `${health.microCir}` : '--'} />
            <InfoCell label="环境温度" value={`${health.envTemp.toFixed(1)} ℃`} />
          </View>
        )}

        <ThemedText type="smallBold">运动传感器</ThemedText>
        <View style={styles.infoGrid}>
          <InfoCell
            label="加速度 XYZ (g)"
            value={imu ? imu.accel.map((v) => v.toFixed(3)).join(' / ') : '--'}
          />
          <InfoCell
            label="角速度 XYZ (°/s)"
            value={imu ? imu.gyro.map((v) => v.toFixed(2)).join(' / ') : '--'}
          />
          <InfoCell
            label="姿态角 XYZ (°)"
            value={imu ? imu.euler.map((v) => v.toFixed(1)).join(' / ') : '--'}
          />
        </View>
        <ThemedText type="smallBold">音频特征</ThemedText>
        <View style={styles.infoGrid}>
          <InfoCell label="RMS" value={audio ? audio.rms.toFixed(4) : '--'} />
          <InfoCell label="过零率" value={audio ? audio.zcr.toFixed(4) : '--'} />
          <InfoCell label="采样率" value={audio ? `${audio.sampleRate} Hz` : '--'} />
        </View>
        {audio && (
          <View style={styles.melGrid}>
            {audio.mel.map((value, index) => (
              <View
                key={index}
                accessibilityLabel={`Mel ${index + 1}: ${value.toFixed(1)}`}
                style={[
                  styles.melCell,
                  {
                    backgroundColor:
                      value > -10
                        ? theme.warning
                        : value > -20
                          ? theme.tint
                          : theme.backgroundSelected,
                  },
                ]}
              >
                <ThemedText type="small" style={{ color: theme.text }}>
                  {value.toFixed(0)}
                </ThemedText>
              </View>
            ))}
          </View>
        )}
        <ThemedText type="smallBold">采集状态</ThemedText>
        <View style={styles.infoGrid}>
          <InfoCell
            label="实验"
            value={experiment ? (experiment.active ? '采集中' : '未开始') : '--'}
          />
          <InfoCell
            label="动作事件"
            value={experiment?.eventActive ? experiment.eventLabel : '无'}
          />
          <InfoCell
            label="SD 卡"
            value={
              experiment
                ? `${experiment.sdLogging ? '写入中' : experiment.sdStatus} · ${experiment.sdRows} 条`
                : '--'
            }
          />
        </View>
        <ThemedText type="small" themeColor="textSecondary" style={styles.footer}>
          {latest
            ? `云端接收：${new Date(latest.receivedAt).toLocaleString()}\n设备运行时间：${(latest.tsMs / 1000).toFixed(1)} 秒${isFresh ? '' : ' · 数据未更新'}`
            : '等待设备数据'}
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
    flexGrow: 1,
    flexBasis: 140,
    borderRadius: 8,
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
    flexWrap: 'wrap',
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
  melGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
  melCell: {
    width: 42,
    height: 32,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 4,
  },
});
