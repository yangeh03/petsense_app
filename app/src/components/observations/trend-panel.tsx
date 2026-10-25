import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Svg, { Circle, Line, Path } from 'react-native-svg';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { chartSegments, clockTime } from '@/services/observation-view';
import type { MetricKey, ObservationPeriod, Observations } from '@/types/observations';

const METRICS: { key: MetricKey; name: string; unit: string; decimals: number }[] = [
  { key: 'heartRate', name: '心率', unit: 'bpm', decimals: 0 },
  { key: 'spo2', name: '血氧', unit: '%', decimals: 0 },
  { key: 'bodyTemp', name: '传感器温度', unit: '℃', decimals: 1 },
];

export function TrendPanel({
  data,
  period,
  onPeriod,
  busy,
}: {
  data: Observations | null;
  period: ObservationPeriod;
  onPeriod: (period: ObservationPeriod) => void;
  busy: boolean;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const [metric, setMetric] = useState<MetricKey>('heartRate');
  const [width, setWidth] = useState(0);
  const config = METRICS.find((item) => item.key === metric)!;
  const validPeriod = data?.period === period;
  const values = validPeriod ? data.buckets.map((b) => b.metrics[metric].avg) : [];
  const points = values.filter((value): value is number => value !== null);
  const min = points.length ? Math.min(...points) : 0;
  const max = points.length ? Math.max(...points) : 1;
  const padding = max === min ? 1 : (max - min) * 0.15;
  const bottom = min - padding;
  const top = max + padding;
  const height = 148;
  const x = (index: number) => 12 + (index / Math.max(1, values.length - 1)) * (width - 24);
  const y = (value: number) => 12 + (1 - (value - bottom) / (top - bottom)) * (height - 24);
  const summary = validPeriod ? data.metrics[metric] : null;
  return (
    <View style={[styles.panel, { backgroundColor: theme.backgroundElement }]}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen(!open)}
        style={styles.heading}
      >
        <View style={styles.titleGroup}>
          <ThemedText style={styles.title}>健康趋势</ThemedText>
          {!open && (
            <ThemedText type="small" themeColor="textSecondary">
              按需查看历史变化
            </ThemedText>
          )}
        </View>
        <ThemedText style={{ color: theme.tint }}>{open ? '收起 −' : '查看 ＋'}</ThemedText>
      </Pressable>
      {open && (
        <View style={styles.body}>
          <View style={styles.row}>
            {(['day', 'week', 'month'] as const).map((value, index) => (
              <Pressable
                key={value}
                accessibilityRole="button"
                accessibilityState={{ selected: period === value }}
                onPress={() => onPeriod(value)}
                style={[
                  styles.chip,
                  { backgroundColor: period === value ? theme.tintBackground : theme.background },
                ]}
              >
                <ThemedText
                  type="smallBold"
                  style={{ color: period === value ? theme.tint : theme.textSecondary }}
                >
                  {['24小时', '7天', '30天'][index]}
                </ThemedText>
              </Pressable>
            ))}
          </View>
          <View style={styles.row}>
            {METRICS.map((item) => (
              <Pressable
                key={item.key}
                onPress={() => setMetric(item.key)}
                accessibilityRole="button"
                accessibilityState={{ selected: item.key === metric }}
                style={[
                  styles.metric,
                  { borderBottomColor: item.key === metric ? theme.tint : theme.backgroundElement },
                ]}
              >
                <ThemedText
                  type="small"
                  style={{ color: item.key === metric ? theme.text : theme.textSecondary }}
                >
                  {item.name}
                </ThemedText>
              </Pressable>
            ))}
          </View>
          <View onLayout={(event) => setWidth(event.nativeEvent.layout.width)} style={styles.chart}>
            {points.length && width > 0 ? (
              <Svg
                width={width}
                height={height}
                accessibilityLabel={`${config.name}历史均值，缺失时段断开`}
              >
                {[0, 1, 2].map((index) => (
                  <Line
                    key={index}
                    x1={0}
                    x2={width}
                    y1={12 + index * 62}
                    y2={12 + index * 62}
                    stroke={theme.backgroundSelected}
                    strokeWidth={1}
                  />
                ))}
                {chartSegments(values).map((segment, index) => (
                  <Path
                    key={index}
                    d={segment
                      .map(
                        (point, pointIndex) =>
                          `${pointIndex ? 'L' : 'M'} ${x(point.index)} ${y(point.value)}`,
                      )
                      .join(' ')}
                    stroke={theme.tint}
                    strokeWidth={2.5}
                    fill="none"
                  />
                ))}
                {values.map(
                  (value, index) =>
                    value !== null && (
                      <Circle key={index} cx={x(index)} cy={y(value)} r={3} fill={theme.tint} />
                    ),
                )}
              </Svg>
            ) : (
              <ThemedText type="small" themeColor="textSecondary">
                {!validPeriod && busy ? '正在加载趋势…' : '这个时段还没有有效读数'}
              </ThemedText>
            )}
          </View>
          {validPeriod && (
            <View style={styles.axis}>
              {[0, Math.floor((data.buckets.length - 1) / 2), data.buckets.length - 1].map(
                (index) => (
                  <ThemedText key={index} type="small" themeColor="textSecondary">
                    {data.buckets[index].label}
                  </ThemedText>
                ),
              )}
            </View>
          )}
          {summary && summary.count > 0 && (
            <View style={styles.stats}>
              {(['avg', 'min', 'max'] as const).map((key, index) => (
                <View key={key} style={styles.stat}>
                  <ThemedText type="small" themeColor="textSecondary">
                    {['平均', '最低', '最高'][index]}
                  </ThemedText>
                  <ThemedText type="smallBold">
                    {summary[key]?.toFixed(config.decimals)}{' '}
                    <ThemedText type="small">{config.unit}</ThemedText>
                  </ThemedText>
                </View>
              ))}
            </View>
          )}
          <ThemedText type="small" themeColor="textSecondary">
            {period === 'day' ? '每点为小时均值' : '每点为日均值'} · 缺失时段留空
            {summary?.count ? ` · ${summary.count}个有效读数` : ''}
          </ThemedText>
          {validPeriod && (
            <ThemedText type="small" themeColor="textSecondary">
              截至 {clockTime(data.range.endAt)} · 历史保留{data.retentionDays}天
            </ThemedText>
          )}
        </View>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  panel: { borderRadius: 22, padding: 20 },
  heading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  titleGroup: { gap: 4, flexShrink: 1 },
  title: { fontSize: 18, fontWeight: '700' },
  body: { gap: 14, marginTop: 20 },
  row: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  chip: { paddingHorizontal: 14, paddingVertical: 10, borderRadius: 16 },
  metric: { paddingVertical: 8, paddingHorizontal: 4, borderBottomWidth: 2 },
  chart: { height: 148, justifyContent: 'center', alignItems: 'center' },
  axis: { flexDirection: 'row', justifyContent: 'space-between' },
  stats: { flexDirection: 'row', gap: 12 },
  stat: { flex: 1, gap: 4 },
});
