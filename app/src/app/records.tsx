import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { SoundTimeline } from '@/components/observations/sound-timeline';
import { TrendPanel } from '@/components/observations/trend-panel';
import { ThemedText } from '@/components/themed-text';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useObservations } from '@/hooks/use-observations';
import { useTheme } from '@/hooks/use-theme';
import { fetchAgentObservation, type AgentObservation } from '@/services/agent';
import { duration } from '@/services/observation-view';
import type { ObservationPeriod } from '@/types/observations';

/** Health history lives here so Today can stay focused on the present moment. */
export default function RecordsScreen() {
  const theme = useTheme();
  const params = useLocalSearchParams<{ petId?: string; start?: string; end?: string }>();
  const originalRange = useRef({ start: params.start, end: params.end });
  const [rangeLevel, setRangeLevel] = useState<'segment' | 'day' | 'week'>('segment');
  const [selected, setSelected] = useState<AgentObservation | null>(null);
  const [rangeError, setRangeError] = useState('');
  useEffect(() => {
    let active = true;
    if (params.petId && params.start && params.end) {
      void fetchAgentObservation(Number(params.petId), params.start, params.end)
        .then((result) => {
          if (active) {
            setSelected(result);
            setRangeError('');
          }
        })
        .catch(() => {
          if (active) setRangeError('暂时无法读取这段依据');
        });
    }
    return () => {
      active = false;
    };
  }, [params.petId, params.start, params.end]);
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [period, setPeriod] = useState<ObservationPeriod>('day');
  const { dashboard, observations, busy, error, reload } = useObservations(period);
  const [refreshing, setRefreshing] = useState(false);
  const pet = dashboard?.pet ?? null;
  const summary = observations?.summary;
  const hasCoverage = !!summary?.firstObservedAt;

  const refresh = () => {
    setRefreshing(true);
    void reload().finally(() => setRefreshing(false));
  };

  const exploreRange = (level: 'segment' | 'day' | 'week') => {
    const original = originalRange.current;
    if (!original.start || !original.end) return;
    let start = new Date(original.start);
    let end = new Date(original.end);
    if (level !== 'segment') {
      const local = new Date(start.getTime() + 8 * 3600000);
      local.setUTCHours(0, 0, 0, 0);
      if (level === 'week') local.setUTCDate(local.getUTCDate() - ((local.getUTCDay() + 6) % 7));
      start = new Date(local.getTime() - 8 * 3600000);
      end = new Date(start.getTime() + (level === 'week' ? 7 : 1) * 86400000);
    }
    setRangeLevel(level);
    router.setParams({ start: start.toISOString(), end: end.toISOString() });
  };

  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.scroll}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.tint} />
      }
    >
      {!router.canGoBack() ? (
        <Pressable
          accessibilityRole="button"
          onPress={() => router.replace('/')}
          style={{ padding: 20 }}
        >
          <ThemedText style={{ color: theme.tint }}>回到今日</ThemedText>
        </Pressable>
      ) : null}
      <View
        style={[
          styles.container,
          {
            paddingTop: insets.top + Spacing.four,
            paddingBottom: insets.bottom + BottomTabInset + 28,
          },
        ]}
      >
        <View style={styles.heading}>
          <ThemedText style={[styles.title, { fontFamily: Fonts.rounded }]}>记录</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            只汇总项圈实际观测到的数据
          </ThemedText>
        </View>

        {selected ? (
          <View style={[styles.card, { backgroundColor: theme.backgroundElement, gap: 8 }]}>
            <View style={{ flexDirection: 'row', gap: 20 }}>
              {(['segment', 'day', 'week'] as const).map((level) => (
                <Pressable
                  key={level}
                  onPress={() => exploreRange(level)}
                  accessibilityRole="button"
                  accessibilityState={{ selected: rangeLevel === level }}
                >
                  <ThemedText
                    style={{ color: rangeLevel === level ? theme.tint : theme.textSecondary }}
                  >
                    {level === 'segment' ? '引用时段' : level === 'day' ? '这一天' : '这一周'}
                  </ThemedText>
                </Pressable>
              ))}
            </View>
            <ThemedText>
              {new Date(selected.startAt).toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                hour12: false,
              })}{' '}
              至{' '}
              {new Date(selected.endAt).toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                hour12: false,
              })}
            </ThemedText>
            <ThemedText>
              已记录声音片段 {selected.soundEventCount} 次 · 声音覆盖{' '}
              {Math.round(selected.soundObservedSeconds / 60)} 分钟
            </ThemedText>
            {Object.entries(selected.metrics)
              .filter(([, metric]) => metric.avg !== null)
              .map(([key, metric]) => (
                <ThemedText key={key}>
                  {(
                    {
                      heartRate: '平均心率',
                      spo2: '平均血氧',
                      bodyTemp: '平均传感器温度',
                      envTemp: '平均环境温度',
                    } as Record<string, string>
                  )[key] ?? key}
                  ：{metric.avg}
                </ThemedText>
              ))}
            <ThemedText>
              {selected.actionObservedSeconds > 0
                ? `实际覆盖 ${Math.round(selected.actionObservedSeconds / 60)} 分钟 · 走动 ${Math.round((selected.actionSeconds.walking ?? 0) / 60)} 分钟`
                : '这段时间还没有足够的活动记录，暂时无法判断活动量'}
            </ThemedText>
          </View>
        ) : null}
        {rangeError ? <ThemedText>{rangeError}</ThemedText> : null}
        {error ? (
          <Pressable
            onPress={() => void reload()}
            style={[styles.notice, { backgroundColor: theme.tintBackground }]}
          >
            <ThemedText type="small">{error}</ThemedText>
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              重新加载
            </ThemedText>
          </Pressable>
        ) : null}

        {!user ? (
          <EmptyCard
            title="登录后查看健康记录"
            detail="登录后可回顾宠物的健康趋势、活动和声音记录。"
            action="登录 / 注册"
            onPress={() => router.push('/login')}
          />
        ) : busy && !dashboard ? (
          <ActivityIndicator color={theme.tint} />
        ) : !pet ? (
          <EmptyCard
            title="先建立宠物档案"
            detail="完成建档并连接项圈后，这里会开始沉淀健康记录。"
            action="建立档案"
            onPress={() => router.push('/setup')}
          />
        ) : (
          <>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <View style={styles.rowBetween}>
                <ThemedText style={styles.sectionTitle}>今日活动</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  {hasCoverage ? `已观测 ${duration(summary!.observedSeconds)}` : '尚未开始观测'}
                </ThemedText>
              </View>
              <View style={[styles.summary, { backgroundColor: theme.background }]}>
                <SummaryMetric
                  label="走动"
                  value={hasCoverage ? duration(summary!.walkingSeconds) : '—'}
                />
                <SummaryMetric
                  label="静止"
                  value={hasCoverage ? duration(summary!.stationarySeconds) : '—'}
                />
                <SummaryMetric
                  label="声音"
                  value={hasCoverage ? `${summary!.soundEpisodeCount} 次` : '—'}
                />
              </View>
            </View>

            <TrendPanel data={observations} period={period} onPeriod={setPeriod} busy={busy} />

            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText style={styles.sectionTitle}>声音记录</ThemedText>
              <SoundTimeline events={observations?.events ?? []} />
            </View>
          </>
        )}
      </View>
    </ScrollView>
  );
}

function EmptyCard({
  title,
  detail,
  action,
  onPress,
}: {
  title: string;
  detail: string;
  action: string;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
      <ThemedText style={styles.sectionTitle}>{title}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {detail}
      </ThemedText>
      <Pressable
        accessibilityRole="button"
        onPress={onPress}
        style={[styles.action, { backgroundColor: theme.tintBackground }]}
      >
        <ThemedText type="smallBold" style={{ color: theme.tint }}>
          {action}
        </ThemedText>
      </Pressable>
    </View>
  );
}

function SummaryMetric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
      <ThemedText style={styles.metricValue}>{value}</ThemedText>
    </View>
  );
}

const styles = StyleSheet.create({
  scroll: { alignItems: 'center' },
  container: {
    width: '100%',
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    gap: Spacing.three,
  },
  heading: { gap: Spacing.one },
  title: { fontSize: 28, lineHeight: 36, fontWeight: '700' },
  card: { borderRadius: 22, padding: Spacing.four, gap: Spacing.three, boxShadow: Shadows.card },
  sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
    flexWrap: 'wrap',
  },
  summary: { flexDirection: 'row', borderRadius: 14, padding: Spacing.three, gap: Spacing.two },
  metric: { flex: 1, gap: Spacing.one },
  metricValue: { fontSize: 17, lineHeight: 24, fontWeight: '700', fontVariant: ['tabular-nums'] },
  action: {
    alignSelf: 'flex-start',
    borderRadius: 14,
    paddingHorizontal: Spacing.four,
    paddingVertical: Spacing.two,
  },
  notice: { borderRadius: 16, padding: Spacing.four, gap: Spacing.one },
});
