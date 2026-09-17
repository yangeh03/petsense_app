import { useMemo, useState } from 'react';
import { StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Defs, LinearGradient, Line, Path, Stop, Circle } from 'react-native-svg';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import type { SeriesPoint } from '@/types/telemetry';

type LineChartProps = {
  points: SeriesPoint[];
  color: string;
  unit: string;
  /** 期望保留的小数位 */
  decimals?: number;
};

type Scale = { min: number; max: number; span: number };

const GRID_LINES = 4;
const Y_PADDING_RATIO = 0.12;

function niceScale(values: number[]): Scale {
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  if (rawMax - rawMin < 1e-6) {
    return { min: rawMin - 1, max: rawMax + 1, span: 2 };
  }
  const pad = (rawMax - rawMin) * Y_PADDING_RATIO;
  return { min: rawMin - pad, max: rawMax + pad, span: rawMax - rawMin + pad * 2 };
}

/** 详情页实时大图表：自适应 Y 轴、水平网格、最新值圆点与数值。 */
export function LineChart({ points, color, unit, decimals = 0 }: LineChartProps) {
  const { width: windowWidth } = useWindowDimensions();
  const chartWidth = Math.min(windowWidth - Spacing.four * 2, 640);
  const chartHeight = 220;
  const [layoutHeight, setLayoutHeight] = useState(chartHeight);
  const height = layoutHeight || chartHeight;

  const scale = useMemo<Scale | null>(() => {
    if (points.length < 2) return null;
    return niceScale(points.map((p) => p.v));
  }, [points]);

  if (!scale) {
    return (
      <View style={[styles.empty, { width: chartWidth, height: chartHeight }]}>
        <ThemedText type="small" themeColor="textSecondary">
          等待数据…
        </ThemedText>
      </View>
    );
  }

  const stepX = chartWidth / (points.length - 1);
  const yFor = (v: number) => height - ((v - scale.min) / scale.span) * height;
  const coords = points.map((p, i) => ({ x: i * stepX, y: yFor(p.v) }));

  let line = `M ${coords[0].x} ${coords[0].y}`;
  for (let i = 1; i < coords.length; i++) {
    const prev = coords[i - 1];
    const cur = coords[i];
    const midX = (prev.x + cur.x) / 2;
    line += ` Q ${midX} ${prev.y} ${midX} ${(prev.y + cur.y) / 2} Q ${midX} ${cur.y} ${cur.x} ${cur.y}`;
  }
  const area = `${line} L ${chartWidth} ${height} L 0 ${height} Z`;
  const last = coords[coords.length - 1];
  const lastPoint = points[points.length - 1];
  const gradientId = 'chart-fill';

  return (
    <View style={{ width: chartWidth }}>
      <View
        onLayout={(e) => setLayoutHeight(Math.round(e.nativeEvent.layout.height))}
        style={styles.chartBox}
      >
        <Svg width={chartWidth} height={height}>
          <Defs>
            <LinearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <Stop offset="0" stopColor={color} stopOpacity={0.3} />
              <Stop offset="1" stopColor={color} stopOpacity={0.02} />
            </LinearGradient>
          </Defs>
          {Array.from({ length: GRID_LINES + 1 }, (_, i) => {
            const ratio = i / GRID_LINES;
            const y = ratio * height;
            return (
              <Line
                key={i}
                x1={0}
                y1={y}
                x2={chartWidth}
                y2={y}
                stroke={color}
                strokeOpacity={0.12}
                strokeWidth={1}
                strokeDasharray={i === GRID_LINES ? undefined : '4 6'}
              />
            );
          })}
          <Path d={area} fill={`url(#${gradientId})`} />
          <Path d={line} fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" />
          <Circle cx={last.x} cy={last.y} r={5} fill={color} />
          <Circle cx={last.x} cy={last.y} r={9} fill={color} fillOpacity={0.2} />
        </Svg>
        {/* 网格刻度值 */}
        <View style={StyleSheet.absoluteFill} pointerEvents="none">
          {Array.from({ length: GRID_LINES + 1 }, (_, i) => {
            const ratio = i / GRID_LINES;
            const value = scale.max - scale.span * ratio;
            const top = Math.round(ratio * height) - 7;
            return (
              <Text
                key={i}
                style={[styles.axisLabel, { top: Math.max(0, Math.min(top, height - 14)) }]}
              >
                {value.toFixed(decimals)}
              </Text>
            );
          })}
        </View>
      </View>
      <View style={styles.footer}>
        <ThemedText type="small" themeColor="textSecondary">
          最近 {points.length} 个采样点
        </ThemedText>
        <ThemedText type="smallBold" style={{ color }}>
          {lastPoint.v.toFixed(decimals)} {unit}
        </ThemedText>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  empty: {
    borderRadius: Spacing.three,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: 'rgba(128,128,128,0.35)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  chartBox: {
    height: 220,
    overflow: 'hidden',
  },
  axisLabel: {
    position: 'absolute',
    left: 2,
    fontSize: 10,
    color: 'rgba(128,128,128,0.8)',
    backgroundColor: 'rgba(0,0,0,0.001)',
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: Spacing.one,
  },
});
