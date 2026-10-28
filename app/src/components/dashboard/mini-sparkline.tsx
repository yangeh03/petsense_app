import Svg, { Polyline } from 'react-native-svg';

import { useTheme } from '@/hooks/use-theme';
import { trendScale } from '@/services/observation-view';
import type { VitalKey } from '@/services/vitals-view';
import type { SeriesPoint } from '@/types/telemetry';

const WIDTH = 100;
const HEIGHT = 26;
const INSET = 3;

/** 形状即信息：迷你趋势线不画坐标轴，今日区间由卡片上的文字表达。 */
export function MiniSparkline({
  points,
  metric,
  accessibilityLabel,
}: {
  points: SeriesPoint[];
  metric: VitalKey;
  accessibilityLabel: string;
}) {
  const theme = useTheme();
  if (points.length < 2) return null;
  const values = points.map((point) => point.v);
  const { min, max } = trendScale(values, metric);
  const span = max - min || 1;
  const coords = points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * WIDTH;
      const y = HEIGHT - INSET - ((point.v - min) / span) * (HEIGHT - INSET * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <Svg
      width="100%"
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      accessibilityRole="image"
      accessibilityLabel={accessibilityLabel}
    >
      <Polyline
        points={coords}
        fill="none"
        stroke={theme.tint}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </Svg>
  );
}
