import { SymbolView } from 'expo-symbols';
import { StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import type { ActivityMetric } from '@/data/pets';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

const iconNames = {
  footsteps: { ios: 'figure.run', android: 'directions_walk', web: 'directions_run' },
  clock: { ios: 'clock.fill', android: 'schedule', web: 'schedule' },
  drop: { ios: 'drop.fill', android: 'water_drop', web: 'water_drop' },
} as const;

type ActivityBarProps = {
  metric: ActivityMetric;
};

/** 活动指标卡片：图标 + 名称 + 目标进度条。 */
export function ActivityBar({ metric }: ActivityBarProps) {
  const theme = useTheme();
  const progress = Math.min(metric.value / metric.goal, 1);

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <SymbolView name={iconNames[metric.icon]} size={16} tintColor={theme.tint} />
        <ThemedText type="small" themeColor="textSecondary" style={styles.label}>
          {metric.label}
        </ThemedText>
        <ThemedText type="smallBold">
          {metric.value}
          <ThemedText type="small" themeColor="textSecondary">
            {' / '}
            {metric.goal} {metric.unit}
          </ThemedText>
        </ThemedText>
      </View>
      <View style={[styles.track, { backgroundColor: theme.backgroundSelected }]}>
        <View
          style={[styles.progress, { width: `${progress * 100}%`, backgroundColor: theme.tint }]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: Spacing.two,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  label: {
    marginRight: 'auto',
  },
  track: {
    height: 8,
    borderRadius: 4,
    overflow: 'hidden',
  },
  progress: {
    height: '100%',
    borderRadius: 4,
  },
});
