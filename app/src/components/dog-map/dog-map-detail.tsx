import { StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { dogMapPointName } from '@/data/dog-map';
import { useTheme } from '@/hooks/use-theme';

import type { DogMapPoint } from '@/types/dog-map';

const METRICS: {
  key: 'gviPercent' | 'sidewalkPercent' | 'trafficPercent' | 'pedestrianPercent';
  label: string;
}[] = [
  { key: 'gviPercent', label: '绿视率' },
  { key: 'sidewalkPercent', label: '人行道' },
  { key: 'trafficPercent', label: '交通暴露' },
  { key: 'pedestrianPercent', label: '人流占比' },
];

/** 选中路段的详情卡：源评分与等级原样展示，不做重算。 */
export function DogMapDetail({ point }: { point: DogMapPoint | null }) {
  const theme = useTheme();
  if (!point) return null;
  const name = dogMapPointName(point);
  const tags = point.tags.join(' · ');
  return (
    <View
      accessibilityLiveRegion="polite"
      style={[
        styles.card,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <ThemedText type="smallBold">
        {name} · {point.score} 分
      </ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {point.grade}
        {tags ? ` · ${tags}` : ''}
      </ThemedText>
      <View style={styles.metrics}>
        {METRICS.map((metric) => (
          <View key={metric.key} style={styles.metricRow}>
            <ThemedText type="small" themeColor="textSecondary">
              {metric.label}
            </ThemedText>
            <ThemedText type="small">{point[metric.key]}%</ThemedText>
          </View>
        ))}
      </View>
    </View>
  );
}
const styles = StyleSheet.create({
  card: {
    width: '100%',
    borderRadius: 14,
    borderWidth: 1,
    padding: 14,
    gap: 4,
  },
  metrics: {
    marginTop: 4,
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  metricRow: {
    minWidth: '45%',
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 8,
    paddingRight: 8,
  },
});
