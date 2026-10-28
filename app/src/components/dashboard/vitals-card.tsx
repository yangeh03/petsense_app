import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { MiniSparkline } from '@/components/dashboard/mini-sparkline';
import { ThemedText } from '@/components/themed-text';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { clockTime } from '@/services/observation-view';
import { formatVital, selectEnvTemp, selectVitals } from '@/services/vitals-view';
import type { MetricKey, MetricSummary, ObservationBucket } from '@/types/observations';
import type { TelemetryHealth, TelemetrySeries } from '@/types/telemetry';

/**
 * 健康体征卡：实时值 + 60 分钟迷你趋势 + 今日区间；实时无效时明确解释，
 * 离线回退当日均值并标注，绝不把缺失渲染成 0。
 */
export function VitalsCard({
  health,
  current,
  daily,
  series,
  buckets,
  updatedAt,
  onOpenCollar,
}: {
  health: TelemetryHealth | null;
  current: boolean;
  daily: Partial<Record<MetricKey, MetricSummary>> | null;
  series: TelemetrySeries;
  buckets: ObservationBucket[];
  updatedAt: string | null;
  onOpenCollar: () => void;
}) {
  const theme = useTheme();
  const [explanationOpen, setExplanationOpen] = useState(false);
  const vitals = selectVitals({ health, current, daily, series, buckets });
  const envTemp = selectEnvTemp({ health, current, daily });
  const liveInvalid = current && !health;
  const hotTemp = health !== null && health.valid === true && health.bodyTemp >= 42;
  return (
    <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
      <View style={styles.rowBetween}>
        <ThemedText style={styles.sectionTitle}>健康体征</ThemedText>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded: explanationOpen }}
          onPress={() => setExplanationOpen(!explanationOpen)}
          style={styles.tap}
        >
          <ThemedText type="small" style={{ color: theme.tint }}>
            {explanationOpen ? '收起说明' : '测量说明'}
          </ThemedText>
        </Pressable>
      </View>
      {liveInvalid && (
        <ThemedText type="small" themeColor="textSecondary">
          项圈在线，传感器暂未读到有效读数，请检查佩戴贴合。
        </ThemedText>
      )}
      <View style={styles.vitals}>
        {vitals.map((vital, index) => (
          <View
            key={vital.key}
            style={[
              styles.vital,
              index > 0 && {
                borderLeftWidth: StyleSheet.hairlineWidth,
                borderLeftColor: theme.backgroundSelected,
                paddingLeft: 10,
              },
            ]}
          >
            <View style={styles.labelRow}>
              <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
                {vital.label}
              </ThemedText>
              {vital.value !== null && vital.source === 'avg' && (
                <ThemedText type="small" style={{ color: theme.warning }}>
                  均值
                </ThemedText>
              )}
            </View>
            <View style={styles.valueRow}>
              <ThemedText
                style={styles.value}
                numberOfLines={1}
                adjustsFontSizeToFit
                minimumFontScale={0.8}
              >
                {formatVital(vital.value, vital.decimals)}
              </ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {vital.unit}
              </ThemedText>
            </View>
            <MiniSparkline
              points={vital.points}
              metric={vital.key}
              accessibilityLabel={`${vital.label}近期趋势，${vital.points.length} 个数据点`}
            />
            <ThemedText
              type="small"
              themeColor="textSecondary"
              numberOfLines={1}
              adjustsFontSizeToFit
              minimumFontScale={0.8}
            >
              {vital.today
                ? `${formatVital(vital.today.min, vital.decimals)}–${formatVital(vital.today.max, vital.decimals)}`
                : '今日暂无统计'}
            </ThemedText>
          </View>
        ))}
      </View>
      {hotTemp && (
        <ThemedText
          type="small"
          style={[
            styles.alertText,
            { color: theme.warning, backgroundColor: theme.tintBackground },
          ]}
        >
          温度读数偏高，请先核对佩戴和传感器状态。
        </ThemedText>
      )}
      {(envTemp !== null || updatedAt !== null) && (
        <View style={[styles.subrow, { backgroundColor: theme.background }]}>
          {envTemp !== null && (
            <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
              环境 {envTemp.value.toFixed(1)}℃
            </ThemedText>
          )}
          {updatedAt !== null && (
            <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
              最近更新 {clockTime(updatedAt)}
            </ThemedText>
          )}
        </View>
      )}
      {explanationOpen && (
        <View style={[styles.explanation, { backgroundColor: theme.background }]}>
          <ThemedText type="small" themeColor="textSecondary">
            数值下方为今日最小–最大区间；温度来自项圈传感器，不等同于体温。趋势线在实时在线时显示最近读数，离线时显示当日小时均值。
          </ThemedText>
          <Pressable accessibilityRole="button" onPress={onOpenCollar} style={styles.tap}>
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              查看项圈状态 ›
            </ThemedText>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { padding: 16, borderRadius: 22, gap: 12, boxShadow: Shadows.card },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: 8,
  },
  sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  tap: { paddingVertical: 6 },
  vitals: { flexDirection: 'row', gap: 10 },
  vital: { flex: 1, gap: 5, minWidth: 0 },
  labelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 4,
    minHeight: 18,
  },
  valueRow: { flexDirection: 'row', alignItems: 'baseline', gap: 4 },
  value: {
    fontSize: 24,
    lineHeight: 32,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
    flexShrink: 1,
  },
  subrow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 9,
    borderRadius: 14,
  },
  explanation: { padding: 14, borderRadius: 14, gap: 10 },
  alertText: {
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    overflow: 'hidden',
  },
});
