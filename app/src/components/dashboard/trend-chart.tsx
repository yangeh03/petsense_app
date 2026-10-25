import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Svg, { Circle, Line, Path, Text as SvgText } from 'react-native-svg';
import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import {
  bucketPosition,
  chartScale,
  chartSegments,
  chartTouchTime,
  nearestBucket,
  trendDate,
} from '@/services/trend-presentation';
import type { TrendBucket, TrendMetric, TrendsResponse } from '@/types/social';

export function TrendChart({
  data,
  metric,
  decimals,
  unit,
  color,
}: {
  data: TrendsResponse;
  metric: TrendMetric;
  decimals: number;
  unit: string;
  color: string;
}) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const [selectedStart, setSelectedStart] = useState<string | null>(null);
  const { buckets, window } = data;
  const start = Date.parse(window.start),
    end = Date.parse(window.end);
  const step = window.granularity === 'hour' ? 3_600_000 : 86_400_000;
  const left = 40,
    right = 10,
    top = 16,
    bottom = 184,
    height = 220;
  const plotWidth = Math.max(1, width - left - right);
  const scale = chartScale(
    buckets.flatMap((b) => (b.metrics[metric].avg == null ? [] : [b.metrics[metric].avg!])),
    decimals,
  );
  const x = (b: TrendBucket) => left + bucketPosition(b, start, end) * plotWidth;
  const y = (value: number) =>
    bottom - ((value - scale.min) / (scale.max - scale.min)) * (bottom - top);
  const existing = buckets.find(
    (b) => Date.parse(b.start) === (selectedStart ? Date.parse(selectedStart) : NaN),
  );
  const selectedTime = selectedStart ? Date.parse(selectedStart) : null;
  // Sparse all-history ranges keep missing days selectable without allocating empty years.
  const selected =
    existing ??
    (selectedTime != null && selectedTime >= start && selectedTime <= end
      ? {
          start: selectedStart!,
          end: new Date(Math.min(end, selectedTime + step)).toISOString(),
          metrics: {
            [metric]: { count: 0, avg: null, min: null, max: null },
          },
        }
      : null);
  const selectAt = (time: number) => {
    if (data.period === 'all') {
      const day = Math.floor((time + 8 * 3_600_000) / step) * step - 8 * 3_600_000;
      setSelectedStart(new Date(Math.max(start, Math.min(day, end))).toISOString());
    } else {
      const index = nearestBucket(buckets, time);
      setSelectedStart(index >= 0 ? buckets[index].start : null);
    }
  };
  const move = (direction: number) => {
    if (!selected) {
      selectAt(end);
      return;
    }
    selectAt(Math.max(start, Math.min(end, Date.parse(selected.start) + direction * step + 1)));
  };
  const valueText = (value: number | null) =>
    value == null ? '--' : `${value.toFixed(decimals)} ${unit}`;
  const axisLabels = [start, (start + end) / 2, end]
    .map((time) => ({ time, text: trendDate(time, window.granularity === 'hour') }))
    .filter((item, i, all) => all.findIndex((other) => other.text === item.text) === i);
  return (
    <View style={styles.container}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="趋势图，轻触选择时间段，也可使用下方上一时段和下一时段按钮"
        onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
        onPress={(event) => {
          const native = event.nativeEvent as { locationX?: number; offsetX?: number };
          selectAt(chartTouchTime(native.locationX, native.offsetX, left, plotWidth, start, end));
        }}
      >
        {width > 0 && (
          <Svg width={width} height={height} pointerEvents="none">
            {scale.ticks.map((value) => (
              <ViewGrid
                key={value}
                label={value.toFixed(decimals)}
                width={width}
                y={y(value)}
                color={theme.textSecondary}
              />
            ))}
            {chartSegments(buckets, metric, step).map((segment, i) => (
              <Path
                key={i}
                d={segment
                  .map((b, n) => `${n ? 'L' : 'M'} ${x(b)} ${y(b.metrics[metric].avg!)}`)
                  .join(' ')}
                stroke={color}
                strokeWidth={2}
                fill="none"
              />
            ))}
            {buckets
              .filter((b) => b.metrics[metric].avg != null)
              .map((b) => (
                <Circle
                  key={b.start}
                  cx={x(b)}
                  cy={y(b.metrics[metric].avg!)}
                  r={
                    Date.parse(b.start) === (selectedStart ? Date.parse(selectedStart) : NaN)
                      ? 5
                      : 3
                  }
                  fill={color}
                />
              ))}
            {selected && (
              <Line
                x1={left + bucketPosition(selected as TrendBucket, start, end) * plotWidth}
                x2={left + bucketPosition(selected as TrendBucket, start, end) * plotWidth}
                y1={top}
                y2={bottom}
                stroke={theme.textSecondary}
                strokeDasharray="3 4"
              />
            )}
            {axisLabels.map((label, i) => (
              <SvgText
                key={label.text}
                x={
                  i === 0
                    ? left
                    : i === axisLabels.length - 1
                      ? width - right
                      : left + plotWidth / 2
                }
                y={211}
                textAnchor={i === 0 ? 'start' : i === axisLabels.length - 1 ? 'end' : 'middle'}
                fontSize={10}
                fill={theme.textSecondary}
              >
                {label.text}
              </SvgText>
            ))}
          </Svg>
        )}
      </Pressable>
      <View
        style={[styles.detail, { backgroundColor: theme.background }]}
        accessibilityLiveRegion="polite"
      >
        {selected ? (
          <>
            <ThemedText type="smallBold">
              {trendDate(selected.start, true)} — {trendDate(selected.end, true)}
            </ThemedText>
            {selected.metrics[metric].count ? (
              <>
                <ThemedText type="small">
                  均值 {valueText(selected.metrics[metric].avg)} · {selected.metrics[metric].count}{' '}
                  个样本
                </ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  最低 {valueText(selected.metrics[metric].min)} · 最高{' '}
                  {valueText(selected.metrics[metric].max)}
                </ThemedText>
              </>
            ) : (
              <ThemedText type="small" themeColor="textSecondary">
                此时段暂无有效记录
              </ThemedText>
            )}
          </>
        ) : null}
        <View style={styles.navigation}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="上一时段"
            onPress={() => move(-1)}
            disabled={selected != null && Date.parse(selected.start) <= start}
            style={styles.navButton}
          >
            <ThemedText type="small" themeColor="tint">
              ‹ 上一时段
            </ThemedText>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="下一时段"
            onPress={() => move(1)}
            disabled={selected != null && Date.parse(selected.end) >= end}
            style={styles.navButton}
          >
            <ThemedText type="small" themeColor="tint">
              下一时段 ›
            </ThemedText>
          </Pressable>
        </View>
      </View>
    </View>
  );
}
function ViewGrid({
  label,
  width,
  y,
  color,
}: {
  label: string;
  width: number;
  y: number;
  color: string;
}) {
  return (
    <>
      <Line
        x1={40}
        x2={width - 10}
        y1={y}
        y2={y}
        stroke={color}
        strokeOpacity={0.16}
        strokeDasharray="3 5"
      />
      <SvgText x={32} y={y + 3} textAnchor="end" fontSize={10} fill={color}>
        {label}
      </SvgText>
    </>
  );
}
const styles = StyleSheet.create({
  container: { gap: 8 },
  detail: { padding: 12, borderRadius: 12, gap: 6, minHeight: 88 },
  navigation: { flexDirection: 'row', justifyContent: 'space-between' },
  navButton: { minHeight: 36, justifyContent: 'center', paddingHorizontal: 4 },
});
