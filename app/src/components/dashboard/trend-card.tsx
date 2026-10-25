import { SymbolView } from 'expo-symbols';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { ThemedText } from '@/components/themed-text';
import { TrendChart } from '@/components/dashboard/trend-chart';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { trendDate } from '@/services/trend-presentation';
import type { TrendMetric, TrendPeriod, TrendsResponse } from '@/types/social';

const METRICS = {
  heartRate: { label: '心率', unit: 'bpm', decimals: 0 },
  spo2: { label: '血氧', unit: '%', decimals: 0 },
  bodyTemp: { label: '体温', unit: '℃', decimals: 1 },
};
const PERIODS = [
  ['day', '24 小时'],
  ['week', '7 天'],
  ['month', '30 天'],
  ['all', '全部'],
] as const;
export function TrendCard({
  data,
  loading,
  error,
  refresh,
  expanded,
  setExpanded,
  period,
  setPeriod,
  metric,
  setMetric,
}: {
  data: TrendsResponse | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  expanded: boolean;
  setExpanded: (value: boolean) => void;
  period: TrendPeriod;
  setPeriod: (value: TrendPeriod) => void;
  metric: TrendMetric;
  setMetric: (value: TrendMetric) => void;
}) {
  const theme = useTheme();
  const info = METRICS[metric];
  const color =
    metric === 'heartRate' ? theme.danger : metric === 'spo2' ? theme.info : theme.warning;
  const summary = data?.summary[metric];
  const coverage = data?.coverage[metric];
  const format = (value: number | null | undefined) =>
    value == null ? '--' : value.toFixed(info.decimals);
  const countUnit = data?.window.granularity === 'hour' ? '小时段' : '天';
  return (
    <View
      style={[styles.card, { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card }]}
    >
      <View style={styles.header}>
        <ThemedText style={styles.title}>健康趋势</ThemedText>
        <View style={styles.actions}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="刷新趋势"
            disabled={loading}
            onPress={() => void refresh()}
            style={styles.iconButton}
          >
            {loading ? (
              <ActivityIndicator size="small" color={theme.tint} />
            ) : (
              <SymbolView
                name={{ ios: 'arrow.clockwise', android: 'refresh', web: 'refresh' }}
                size={18}
                tintColor={theme.tint}
              />
            )}
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={expanded ? '收起趋势统计' : '展开趋势统计'}
            accessibilityState={{ expanded }}
            onPress={() => setExpanded(!expanded)}
            style={styles.iconButton}
          >
            <SymbolView
              name={{ ios: 'chevron.down', android: 'expand_more', web: 'expand_more' }}
              size={16}
              tintColor={theme.textSecondary}
              style={{ transform: [{ rotate: expanded ? '180deg' : '0deg' }] }}
            />
          </Pressable>
        </View>
      </View>
      {expanded ? (
        <>
          <View style={styles.row}>
            {(Object.keys(METRICS) as TrendMetric[]).map((key) => (
              <Choice
                key={key}
                label={METRICS[key].label}
                selected={metric === key}
                onPress={() => setMetric(key)}
              />
            ))}
          </View>
          <View style={styles.row}>
            {PERIODS.map(([key, label]) => (
              <Choice
                key={key}
                label={label}
                selected={period === key}
                onPress={() => setPeriod(key)}
              />
            ))}
          </View>
          {error ? (
            <View
              style={[styles.notice, { backgroundColor: theme.tintBackground }]}
              accessibilityLiveRegion="polite"
            >
              <ThemedText type="small">
                {data ? '更新失败，当前为上次记录' : '趋势加载失败'}
              </ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {error}
              </ThemedText>
              <Pressable
                accessibilityRole="button"
                onPress={() => void refresh()}
                style={styles.retry}
              >
                <ThemedText type="smallBold" themeColor="tint">
                  重新加载
                </ThemedText>
              </Pressable>
            </View>
          ) : null}
          {!data && loading ? (
            <View style={styles.loading}>
              <ActivityIndicator color={theme.tint} />
            </View>
          ) : null}
          {data && (
            <>
              <ThemedText type="small" themeColor="textSecondary">
                {trendDate(data.window.start, period === 'day')} —{' '}
                {trendDate(data.window.end, period === 'day')}
              </ThemedText>
              <View style={styles.stats}>
                {(['avg', 'min', 'max'] as const).map((key, i) => (
                  <View key={key} style={[styles.stat, { backgroundColor: theme.background }]}>
                    <ThemedText type="small" themeColor="textSecondary">
                      {['平均', '最低', '最高'][i]}
                    </ThemedText>
                    <ThemedText style={styles.statValue}>{format(summary?.[key])}</ThemedText>
                    <ThemedText type="small" themeColor="textSecondary">
                      {info.unit}
                    </ThemedText>
                  </View>
                ))}
              </View>
              {summary?.count ? (
                <TrendChart
                  key={`${period}/${metric}`}
                  data={data}
                  metric={metric}
                  decimals={info.decimals}
                  unit={info.unit}
                  color={color}
                />
              ) : (
                <View style={[styles.empty, { backgroundColor: theme.background }]}>
                  <ThemedText type="smallBold">此范围暂无有效{info.label}记录</ThemedText>
                </View>
              )}
              <ThemedText type="small" themeColor="textSecondary">
                有记录 {coverage?.recordedBuckets ?? 0}/{coverage?.totalBuckets ?? 0} 个{countUnit}{' '}
                · 更新于 {trendDate(data.generatedAt, true)}
              </ThemedText>
            </>
          )}
        </>
      ) : (
        <ThemedText type="small" themeColor="textSecondary">
          {summary?.count
            ? `${info.label}平均 ${format(summary.avg)} ${info.unit}`
            : '暂无趋势记录'}
        </ThemedText>
      )}
    </View>
  );
}
function Choice({
  label,
  selected,
  onPress,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected }}
      onPress={onPress}
      style={[
        styles.choice,
        {
          backgroundColor: selected ? theme.tintBackground : theme.background,
          borderColor: selected ? theme.tint : theme.background,
        },
      ]}
    >
      <ThemedText type="smallBold" style={{ color: selected ? theme.tint : theme.textSecondary }}>
        {label}
      </ThemedText>
    </Pressable>
  );
}
const styles = StyleSheet.create({
  card: { padding: 16, borderRadius: 22, gap: 12 },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  title: { fontSize: 18, fontWeight: '700' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  actions: { flexDirection: 'row' },
  iconButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  choice: {
    minHeight: 40,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 12,
    borderWidth: 1,
    justifyContent: 'center',
  },
  stats: { flexDirection: 'row', gap: 8 },
  stat: { flex: 1, borderRadius: 12, padding: 10, alignItems: 'center', gap: 4 },
  statValue: { fontSize: 22, fontWeight: '700', lineHeight: 28 },
  empty: { padding: 16, borderRadius: 12, gap: 8 },
  notice: { padding: 12, borderRadius: 12, gap: 4 },
  retry: { minHeight: 40, justifyContent: 'center' },
  loading: { minHeight: 150, alignItems: 'center', justifyContent: 'center', gap: 12 },
});
