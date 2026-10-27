import { useRouter } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { DiaryModal } from '@/components/dashboard/diary-modal';
import { SoundTimeline } from '@/components/observations/sound-timeline';
import { TrendPanel } from '@/components/observations/trend-panel';
import { ThemedText } from '@/components/themed-text';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useObservations } from '@/hooks/use-observations';
import { useTheme } from '@/hooks/use-theme';
import { duration } from '@/services/observation-view';
import type { ObservationPeriod } from '@/types/observations';

/** Health history lives here so Today can stay focused on the present moment. */
export default function RecordsScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const [period, setPeriod] = useState<ObservationPeriod>('day');
  const { dashboard, observations, busy, error, reload } = useObservations(period);
  const [refreshing, setRefreshing] = useState(false);
  const [diaryVisible, setDiaryVisible] = useState(false);
  const pet = dashboard?.pet ?? null;
  const summary = observations?.summary;
  const hasCoverage = !!summary?.firstObservedAt;

  const refresh = () => {
    setRefreshing(true);
    void reload().finally(() => setRefreshing(false));
  };

  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.scroll}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.tint} />
      }
    >
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

            <Pressable
              accessibilityRole="button"
              accessibilityLabel="查看运动日记"
              onPress={() => setDiaryVisible(true)}
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
            >
              <ThemedText style={styles.sectionTitle}>运动日记 ›</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {pet.petgif_id
                  ? '把当天真实活动整理成一段可分享的日记。'
                  : '可查看已有日记；制作新日记需要专属数字形象。'}
              </ThemedText>
            </Pressable>
          </>
        )}
      </View>
      {pet ? (
        <DiaryModal
          visible={diaryVisible}
          onClose={() => setDiaryVisible(false)}
          onShared={() => {
            setDiaryVisible(false);
            void reload();
          }}
          petName={pet.name}
          petId={pet.id}
          userId={user?.id ?? -1}
          petgifId={pet.petgif_id ?? null}
          activeMinutes={Math.floor((summary?.walkingSeconds ?? 0) / 60)}
        />
      ) : null}
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
