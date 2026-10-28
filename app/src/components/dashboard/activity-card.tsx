import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { duration } from '@/services/observation-view';
import { timelineSegments, walkingShare } from '@/services/timeline-view';
import type { DayTimeline } from '@/types/observations';

type ObservationsSummary = {
  observedSeconds: number;
  walkingSeconds: number;
  stationarySeconds: number;
};

/** 今日活动卡：时间轴让"什么时候走了、哪里没数据"直接可见，数字只做摘要。 */
export function ActivityCard({
  summary,
  timeline,
  hasCoverage,
  onPress,
}: {
  summary: ObservationsSummary | null;
  timeline: DayTimeline | null | undefined;
  hasCoverage: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  const segments = timelineSegments(timeline);
  const share = walkingShare(summary);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel="查看完整健康记录"
      onPress={onPress}
      style={[styles.card, { backgroundColor: theme.backgroundElement }]}
    >
      <View style={styles.rowBetween}>
        <ThemedText style={styles.sectionTitle}>今日记录</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">
          {hasCoverage && summary ? `已观测${duration(summary.observedSeconds)}` : '尚未开始观测'}
        </ThemedText>
      </View>
      {segments.length > 0 && (
        <>
          <View
            style={styles.timeline}
            accessibilityRole="image"
            accessibilityLabel="今日活动时间轴：走动、静止与无数据时段分布"
          >
            {segments.map((segment) => (
              <View
                key={segment.key}
                style={[
                  styles.segment,
                  segment.state === 'walk' && { backgroundColor: theme.tint, minWidth: 3 },
                  segment.state === 'idle' && { backgroundColor: theme.backgroundSelected },
                  segment.state === 'none' && { backgroundColor: `${theme.textSecondary}1F` },
                  { flexGrow: segment.minutes },
                ]}
              />
            ))}
          </View>
          <View style={styles.legend}>
            <LegendDot color={theme.tint} label="走动" />
            <LegendDot color={theme.backgroundSelected} label="静止" />
            <LegendDot color={`${theme.textSecondary}1F`} label="无数据" />
          </View>
        </>
      )}
      <View style={styles.summary}>
        <View style={[styles.tile, { backgroundColor: theme.background }]}>
          <ThemedText type="small" themeColor="textSecondary">
            走动
          </ThemedText>
          <ThemedText style={styles.tileValue}>
            {hasCoverage && summary ? duration(summary.walkingSeconds) : '—'}
          </ThemedText>
          {share !== null && (
            <ThemedText type="small" style={{ color: theme.tint }}>
              {share >= 1 ? `占已观测 ${share}%` : '不足已观测 1%'}
            </ThemedText>
          )}
        </View>
        <View style={[styles.tile, { backgroundColor: theme.background }]}>
          <ThemedText type="small" themeColor="textSecondary">
            静止
          </ThemedText>
          <ThemedText style={styles.tileValue}>
            {hasCoverage && summary ? duration(summary.stationarySeconds) : '—'}
          </ThemedText>
        </View>
      </View>
      <ThemedText type="small" themeColor="textSecondary">
        按项圈动作识别估算，仅统计项圈在线且识别有效的时段。
      </ThemedText>
      <ThemedText type="smallBold" style={{ color: theme.tint }}>
        查看健康趋势、声音与运动日记 ›
      </ThemedText>
    </Pressable>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <View style={styles.legendItem}>
      <View style={[styles.legendSwatch, { backgroundColor: color }]} />
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
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
  timeline: {
    flexDirection: 'row',
    height: 14,
    borderRadius: 7,
    overflow: 'hidden',
    gap: 1,
  },
  segment: { height: '100%' },
  legend: { flexDirection: 'row', gap: 14 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  legendSwatch: { width: 8, height: 8, borderRadius: 2 },
  summary: { flexDirection: 'row', gap: 10 },
  tile: { flex: 1, borderRadius: 14, padding: 12, gap: 3 },
  tileValue: { fontSize: 17, lineHeight: 24, fontWeight: '700', fontVariant: ['tabular-nums'] },
});
