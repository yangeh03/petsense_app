import { StyleSheet, View } from 'react-native';
import Svg, { Defs, LinearGradient, Path, Stop } from 'react-native-svg';

import type { SeriesPoint } from '@/types/telemetry';

type SparklineProps = {
  points: SeriesPoint[];
  color: string;
  width?: number;
  height?: number;
};

/** 迷你趋势曲线：平滑折线 + 向下渐变填充，无坐标轴，用于卡片内预览。 */
export function Sparkline({ points, color, width = 120, height = 40 }: SparklineProps) {
  if (points.length < 2) {
    return <View style={[styles.placeholder, { width, height, backgroundColor: color }]} />;
  }

  const values = points.map((p) => p.v);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (max - min < 1e-6) {
    min -= 1;
    max += 1;
  }
  const padding = height * 0.15;
  const usable = height - padding * 2;
  const stepX = width / (points.length - 1);
  const y = (v: number) => height - padding - ((v - min) / (max - min)) * usable;
  const coords = values.map((v, i) => ({ x: i * stepX, y: y(v) }));

  let line = `M ${coords[0].x} ${coords[0].y}`;
  for (let i = 1; i < coords.length; i++) {
    const prev = coords[i - 1];
    const cur = coords[i];
    const midX = (prev.x + cur.x) / 2;
    line += ` Q ${midX} ${prev.y} ${midX} ${(prev.y + cur.y) / 2} Q ${midX} ${cur.y} ${cur.x} ${cur.y}`;
  }
  const area = `${line} L ${width} ${height} L 0 ${height} Z`;
  const gradientId = `spark-${color.replace('#', '')}`;

  return (
    <View style={{ width, height }}>
      <Svg width={width} height={height}>
        <Defs>
          <LinearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor={color} stopOpacity={0.35} />
            <Stop offset="1" stopColor={color} stopOpacity={0.02} />
          </LinearGradient>
        </Defs>
        <Path d={area} fill={`url(#${gradientId})`} />
        <Path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" />
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  placeholder: {
    borderRadius: 8,
    opacity: 0.4,
  },
});
