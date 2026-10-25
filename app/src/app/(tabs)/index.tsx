import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useState, type ComponentProps } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { DiaryModal } from '@/components/dashboard/diary-modal';
import { NotificationBell } from '@/components/alerts/notification-bell';
import { LineChart } from '@/components/live/line-chart';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAlerts, worstSeverity } from '@/hooks/use-alerts';
import { useAuth } from '@/hooks/use-auth';
import { useTelemetry } from '@/hooks/use-telemetry';
import { usePetAction } from '@/hooks/use-pet-action';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl, fetchDashboard, fetchPets, fetchTrends } from '@/services/api';
import type { DashboardData, SocialPet, TrendBucket, TrendPeriod } from '@/types/social';
import type { SeriesKey } from '@/types/telemetry';

type SymbolName = ComponentProps<typeof SymbolView>['name'];

const ACTION_LABELS: Record<string, string> = {
  run: '奔跑中',
  walk: '散步中',
  stand: '站立',
  sit: '坐着',
  lie_down: '趴着',
  sniff: '嗅闻',
  jump: '跳跃',
  eat: '进食',
  sleep: '睡觉',
};

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

/**
 * 今日页：设计稿风格（暖色圆角卡 / 柔和投影 / 圆体大字 / 悬浮芯片），
 * 内容为设备真实在测的体征遥测 + PetGIF 数字形象 + 时间维度统计趋势 + 运动日记。
 */
export default function DashboardScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const { activeAlerts } = useAlerts();
  const { latest, displayState } = useTelemetry();
  const [pets, setPets] = useState<SocialPet[]>([]);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [diaryVisible, setDiaryVisible] = useState(false);
  // 板块默认折叠：首屏聚焦 Hero，点标题行展开
  const [vitalsOpen, setVitalsOpen] = useState(false);
  const [trendOpen, setTrendOpen] = useState(false);
  const [trendMetric, setTrendMetric] = useState<SeriesKey>('heartRate');
  const [trendPeriod, setTrendPeriod] = useState<TrendPeriod>('all');
  const [trendBuckets, setTrendBuckets] = useState<TrendBucket[]>([]);
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendWidth, setTrendWidth] = useState(0);

  const load = useCallback(
    async (petId?: number) => {
      if (!user) return;
      try {
        const petData = await fetchPets();
        setPets(petData.pets);
        const targetId = petId ?? petData.pets[0]?.id;
        const data = await fetchDashboard(targetId);
        setDashboard(data);
      } catch {
        // 后端不可达时保留旧数据，下拉重试
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [user],
  );

  useFocusEffect(
    useCallback(() => {
      if (user) {
        void load();
      } else {
        setLoading(false);
        setDashboard(null);
      }
    }, [user, load]),
  );

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    Promise.resolve()
      .then(() => {
        if (!cancelled) setTrendLoading(true);
        return fetchTrends(trendPeriod);
      })
      .then((data) => {
        if (!cancelled) setTrendBuckets(data.buckets);
      })
      .catch(() => {
        if (!cancelled) setTrendBuckets([]);
      })
      .finally(() => {
        if (!cancelled) setTrendLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user, trendPeriod]);

  const pet = dashboard?.pet ?? null;
  const petName = pet?.name ?? '毛孩子';
  const liveForPet = latest && pet?.device_id && latest.deviceId === pet.device_id ? latest : null;
  // 动作推断用帧：优先绑定设备，其次任意在线设备（演示宠物也有动作可看）
  const latestForAction = liveForPet ?? latest;
  // 在线判定：实时流仍在更新且属于绑定宠物才算在线（陈旧帧不算），否则回退后端 60s 窗口判定
  const online =
    displayState === 'streaming' && !!liveForPet ? true : (dashboard?.latest?.online ?? false);

  // 有效体征：优先实时流（valid 帧），否则回退后端转发的最近有效帧
  const vitals =
    (liveForPet?.health?.valid ? liveForPet.health : null) ??
    (dashboard?.latest?.valid ? dashboard.latest : null);
  const fmt = (value: number | undefined | null, digits = 0) =>
    value && value > 0 ? value.toFixed(digits) : '--';
  const batteryPercent =
    liveForPet?.battery?.found || dashboard?.latest?.batteryPercent
      ? Math.round(liveForPet?.battery?.percent ?? dashboard?.latest?.batteryPercent ?? 0)
      : null;

  // 数字宠物：优先 AI 角色卡头像，其次按「实时行为」播放动作 GIF
  const action = usePetAction(latestForAction);
  const heroGifUri = useMemo(() => {
    const behaviors = dashboard?.petgif?.behaviors;
    if (!behaviors) return null;
    return (
      behaviors[action]?.gifUrl ??
      Object.values(behaviors).find((item) => item?.gifUrl)?.gifUrl ??
      null
    );
  }, [dashboard?.petgif, action]);
  const heroAvatarImage = pet?.avatarImageUrl ? absoluteUrl(pet.avatarImageUrl) : null;

  const worst = worstSeverity(activeAlerts);
  const ringColor =
    worst === 'critical'
      ? theme.danger
      : worst === 'warning'
        ? theme.warning
        : worst === 'info'
          ? theme.info
          : online
            ? theme.online
            : theme.textSecondary;

  // 趋势统计：均值曲线 + 平均/最低/最高
  const TREND_METRICS: Record<
    SeriesKey,
    {
      label: string;
      unit: string;
      decimals: number;
      icon: SymbolName;
      color: string;
      pick: (bucket: TrendBucket) => number | null;
    }
  > = {
    heartRate: {
      label: '心率',
      unit: 'bpm',
      decimals: 0,
      icon: { ios: 'heart.fill', android: 'favorite', web: 'favorite' },
      color: theme.danger,
      pick: (bucket) => bucket.avgHr,
    },
    spo2: {
      label: '血氧',
      unit: '%',
      decimals: 0,
      icon: { ios: 'lungs.fill', android: 'air', web: 'air' },
      color: theme.info,
      pick: (bucket) => bucket.avgSpo2,
    },
    bodyTemp: {
      label: '体温',
      unit: '℃',
      decimals: 1,
      icon: { ios: 'thermometer.medium', android: 'device_thermostat', web: 'device_thermostat' },
      color: theme.warning,
      pick: (bucket) => bucket.avgTemp,
    },
  };
  const trend = TREND_METRICS[trendMetric];

  const trendPoints = trendBuckets
    .map((bucket) => ({ v: trend.pick(bucket), label: bucket.label }))
    .filter((item): item is { v: number; label: string } => item.v !== null && item.v > 0)
    .map((item, index) => ({ ...item, t: index }));
  const trendAvg = trendPoints.length
    ? trendPoints.reduce((sum, point) => sum + point.v, 0) / trendPoints.length
    : null;
  const trendMin = trendPoints.length ? Math.min(...trendPoints.map((point) => point.v)) : null;
  const trendMax = trendPoints.length ? Math.max(...trendPoints.map((point) => point.v)) : null;

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => {
            setRefreshing(true);
            void load();
          }}
        />
      }
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.two }]}>
        <View style={styles.topBar}>
          <ThemedText type="small" themeColor="textSecondary">
            {new Date().getMonth() + 1} 月 {new Date().getDate()} 日
          </ThemedText>
          <NotificationBell />
        </View>

        <ThemedText style={[styles.greeting, { fontFamily: Fonts.rounded }]}>
          {greeting()}，{petName}！
        </ThemedText>
        <ThemedText type="small" themeColor="textSecondary" style={styles.greetingSub}>
          出门散步准备好了吗 🐾
        </ThemedText>

        {authLoading || (loading && user) ? (
          <View style={styles.center}>
            <ActivityIndicator color={theme.tint} />
          </View>
        ) : !user ? (
          <GuideCard
            emoji="🐾"
            title="登录后查看宠物实时状态"
            text="登录并添加宠物后，这里会展示它的实时健康与每日动态"
            buttonLabel="去登录"
            onPress={() => router.push('/login')}
          />
        ) : !pet ? (
          <GuideCard
            emoji="🐶"
            title="还没有添加宠物"
            text="添加宠物并绑定项圈设备，开始记录它的健康日常"
            buttonLabel="添加宠物"
            onPress={() => router.push('/create')}
          />
        ) : (
          <>
            {pets.length > 1 && (
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.petSwitcher}
              >
                {pets.map((item) => {
                  const selected = item.id === pet.id;
                  return (
                    <Pressable
                      key={item.id}
                      onPress={() => {
                        void fetchDashboard(item.id).then(setDashboard);
                      }}
                      style={[
                        styles.petChip,
                        { backgroundColor: selected ? theme.tint : theme.backgroundElement },
                        selected && { boxShadow: Shadows.chip },
                      ]}
                    >
                      <ThemedText type="small" style={selected ? { color: '#ffffff' } : undefined}>
                        {item.avatar_emoji} {item.name}
                      </ThemedText>
                    </Pressable>
                  );
                })}
              </ScrollView>
            )}

            {/* Hero：心率/血氧悬浮芯片 + 数字宠物 + ONLINE 徽章 */}
            <View style={styles.hero}>
              <FloatingChip
                icon={{ ios: 'lungs.fill', android: 'air', web: 'air' }}
                iconColor={theme.info}
                value={fmt(vitals?.spo2)}
                label="血氧 %"
                style={styles.chipLeft}
              />
              <FloatingChip
                icon={{ ios: 'heart.fill', android: 'favorite', web: 'favorite' }}
                iconColor={theme.danger}
                value={fmt(vitals?.heartRate)}
                label="心率 bpm"
                style={styles.chipRight}
              />

              <View
                style={[
                  styles.avatarRing,
                  { borderColor: ringColor, backgroundColor: theme.tintBackground },
                ]}
              >
                <View style={[styles.avatarInner, { borderColor: theme.backgroundElement }]}>
                  {heroGifUri ? (
                    <Image
                      source={{ uri: heroGifUri }}
                      style={styles.avatarGif}
                      contentFit="cover"
                      autoplay={true}
                    />
                  ) : heroAvatarImage ? (
                    <Image
                      source={{ uri: heroAvatarImage }}
                      style={styles.avatarGif}
                      contentFit="cover"
                    />
                  ) : (
                    <ThemedText style={styles.avatarEmoji}>{pet.avatar_emoji}</ThemedText>
                  )}
                </View>
                <View
                  style={[
                    styles.onlineBadge,
                    { backgroundColor: theme.backgroundElement, boxShadow: Shadows.chip },
                  ]}
                >
                  <View
                    style={[
                      styles.onlineDot,
                      { backgroundColor: online ? theme.online : theme.textSecondary },
                    ]}
                  />
                  <ThemedText type="smallBold" style={styles.onlineText}>
                    {online ? `ONLINE · ${ACTION_LABELS[action]}` : 'OFFLINE 离线'}
                  </ThemedText>
                </View>
              </View>
            </View>

            {/* 生命体征主卡（默认折叠，标题行展开） */}
            <View
              style={[
                styles.vitalsCard,
                { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
              ]}
            >
              <Pressable
                style={styles.sectionHead}
                onPress={() => setVitalsOpen((value) => !value)}
                accessibilityLabel={vitalsOpen ? '收起生命体征' : '展开生命体征'}
              >
                <ThemedText style={[styles.vitalsTitle, { fontFamily: Fonts.rounded }]}>
                  生命体征
                </ThemedText>
                <View
                  style={[
                    styles.livePill,
                    { backgroundColor: online ? `${theme.online}14` : theme.backgroundSelected },
                  ]}
                >
                  <View
                    style={[
                      styles.liveDot,
                      { backgroundColor: online ? theme.online : theme.textSecondary },
                    ]}
                  />
                  <ThemedText
                    type="smallBold"
                    style={{
                      fontSize: 11,
                      lineHeight: 14,
                      color: online ? theme.online : theme.textSecondary,
                    }}
                  >
                    {online ? '实时' : '离线'}
                  </ThemedText>
                </View>
                <SymbolView
                  name={{ ios: 'chevron.down', android: 'expand_more', web: 'expand_more' }}
                  size={15}
                  tintColor={theme.textSecondary}
                  style={{ transform: [{ rotate: vitalsOpen ? '180deg' : '0deg' }] }}
                />
              </Pressable>

              {vitalsOpen ? (
                <>
                  <View style={styles.vitalsGrid}>
                    <VitalCell
                      icon={{ ios: 'heart.fill', android: 'favorite', web: 'favorite' }}
                      color={theme.danger}
                      label="心率"
                      value={fmt(vitals?.heartRate)}
                      unit="bpm"
                    />
                    <VitalCell
                      icon={{ ios: 'lungs.fill', android: 'air', web: 'air' }}
                      color={theme.info}
                      label="血氧"
                      value={fmt(vitals?.spo2)}
                      unit="%"
                    />
                    <VitalCell
                      icon={{
                        ios: 'thermometer.medium',
                        android: 'device_thermostat',
                        web: 'device_thermostat',
                      }}
                      color={theme.warning}
                      label="体温"
                      value={fmt(vitals?.bodyTemp, 1)}
                      unit="℃"
                    />
                    <VitalCell
                      icon={{ ios: 'wind', android: 'air', web: 'air' }}
                      color={theme.tint}
                      label="呼吸"
                      value={fmt(vitals?.respiration)}
                      unit="次/分"
                    />
                    <VitalCell
                      icon={{
                        ios: 'heart.text.square',
                        android: 'monitor_heart',
                        web: 'monitor_heart',
                      }}
                      color={theme.online}
                      label="HRV"
                      value={fmt(vitals?.hrvSdnn)}
                      unit="ms"
                    />
                    <VitalCell
                      icon={{ ios: 'sun.max', android: 'wb_sunny', web: 'wb_sunny' }}
                      color={theme.textSecondary}
                      label="环境温度"
                      value={fmt(vitals?.envTemp, 1)}
                      unit="℃"
                    />
                  </View>

                  <View style={[styles.miniRow, { borderTopColor: `${theme.textSecondary}1A` }]}>
                    <MiniPill
                      label="血压"
                      value={
                        vitals?.systolic
                          ? `${Math.round(vitals.systolic)}/${Math.round(vitals.diastolic ?? 0)}`
                          : '--'
                      }
                    />
                    <MiniPill label="微循环" value={fmt(vitals?.microCir)} />
                    <MiniPill label="疲劳度" value={fmt(vitals?.fatigue)} />
                    <MiniPill
                      label="电量"
                      value={batteryPercent !== null ? `${batteryPercent}%` : '--'}
                    />
                  </View>
                </>
              ) : (
                <ThemedText type="small" themeColor="textSecondary" style={styles.sectionSummary}>
                  心率 {fmt(vitals?.heartRate)} · 血氧 {fmt(vitals?.spo2)}% · 体温{' '}
                  {fmt(vitals?.bodyTemp, 1)}℃ · 呼吸 {fmt(vitals?.respiration)}
                </ThemedText>
              )}
            </View>

            {/* 趋势统计（默认折叠，标题行展开） */}
            <View
              style={[
                styles.trendCard,
                { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
              ]}
            >
              <Pressable
                style={styles.sectionHead}
                onPress={() => setTrendOpen((value) => !value)}
                accessibilityLabel={trendOpen ? '收起趋势统计' : '展开趋势统计'}
              >
                <ThemedText style={[styles.trendTitle, { fontFamily: Fonts.rounded }]}>
                  趋势统计
                </ThemedText>
                <SymbolView
                  name={{ ios: 'chevron.down', android: 'expand_more', web: 'expand_more' }}
                  size={15}
                  tintColor={theme.textSecondary}
                  style={{ transform: [{ rotate: trendOpen ? '180deg' : '0deg' }] }}
                />
              </Pressable>

              {trendOpen ? (
                <>
                  <View style={styles.trendHeadRow}>
                    <View style={styles.trendSwitch}>
                      {(Object.keys(TREND_METRICS) as SeriesKey[]).map((key) => {
                        const selected = key === trendMetric;
                        return (
                          <Pressable key={key} onPress={() => setTrendMetric(key)}>
                            <View
                              style={[
                                styles.trendChip,
                                selected && { backgroundColor: theme.tint },
                              ]}
                            >
                              <ThemedText
                                type="smallBold"
                                style={[styles.trendChipText, selected && { color: '#ffffff' }]}
                              >
                                {TREND_METRICS[key].label}
                              </ThemedText>
                            </View>
                          </Pressable>
                        );
                      })}
                    </View>
                    <View style={styles.periodRow}>
                      {(
                        [
                          ['day', '24小时'],
                          ['week', '7天'],
                          ['month', '30天'],
                          ['all', '全部'],
                        ] as const
                      ).map(([period, label]) => {
                        const selected = trendPeriod === period;
                        return (
                          <Pressable key={period} onPress={() => setTrendPeriod(period)}>
                            <View
                              style={[
                                styles.periodChip,
                                selected && { backgroundColor: theme.tint },
                              ]}
                            >
                              <ThemedText
                                type="smallBold"
                                style={[styles.trendChipText, selected && { color: '#ffffff' }]}
                              >
                                {label}
                              </ThemedText>
                            </View>
                          </Pressable>
                        );
                      })}
                    </View>
                  </View>

                  <View
                    style={styles.trendChart}
                    onLayout={(event) => {
                      const { width } = event.nativeEvent.layout;
                      if (width > 0 && width !== trendWidth) setTrendWidth(width);
                    }}
                  >
                    {trendLoading ? (
                      <ActivityIndicator
                        color={theme.tint}
                        style={{ height: 150, width: '100%' }}
                      />
                    ) : (
                      trendWidth > 0 && (
                        <LineChart
                          points={trendPoints}
                          color={trend.color}
                          unit={trend.unit}
                          decimals={trend.decimals}
                          width={trendWidth}
                          height={150}
                        />
                      )
                    )}
                  </View>

                  {trendPoints.length >= 2 && (
                    <View style={styles.trendLabels}>
                      <ThemedText
                        type="small"
                        themeColor="textSecondary"
                        style={styles.trendLabelText}
                      >
                        {trendPoints[0].label}
                      </ThemedText>
                      <ThemedText
                        type="small"
                        themeColor="textSecondary"
                        style={styles.trendLabelText}
                      >
                        {trendPoints[Math.floor(trendPoints.length / 2)].label}
                      </ThemedText>
                      <ThemedText
                        type="small"
                        themeColor="textSecondary"
                        style={styles.trendLabelText}
                      >
                        {trendPoints[trendPoints.length - 1].label}
                      </ThemedText>
                    </View>
                  )}

                  {trendAvg !== null && (
                    <View style={styles.summaryRow}>
                      <SummaryCell label="平均" value={trendAvg.toFixed(trend.decimals)} />
                      <SummaryCell
                        label="最低"
                        value={trendMin !== null ? trendMin.toFixed(trend.decimals) : '--'}
                      />
                      <SummaryCell
                        label="最高"
                        value={trendMax !== null ? trendMax.toFixed(trend.decimals) : '--'}
                      />
                    </View>
                  )}
                </>
              ) : (
                <ThemedText type="small" themeColor="textSecondary" style={styles.sectionSummary}>
                  {trendAvg !== null
                    ? `${trend.label}平均 ${trendAvg.toFixed(trend.decimals)} ${trend.unit} · 最低 ${trendMin?.toFixed(trend.decimals) ?? '--'} · 最高 ${trendMax?.toFixed(trend.decimals) ?? '--'}`
                    : '暂无统计记录'}
                </ThemedText>
              )}
            </View>

            {/* 运动日记入口 */}
            <Pressable
              onPress={() => setDiaryVisible(true)}
              style={[
                styles.diaryEntry,
                { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
              ]}
            >
              <SymbolView
                name={{ ios: 'book.pages', android: 'menu_book', web: 'menu_book' }}
                size={20}
                tintColor={theme.tint}
              />
              <View style={styles.diaryTexts}>
                <ThemedText type="smallBold">生成运动日记</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  把今天的活跃生成手账风海报
                </ThemedText>
              </View>
              <SymbolView
                name={{ ios: 'chevron.right', android: 'chevron_right', web: 'chevron_right' }}
                size={14}
                tintColor={theme.textSecondary}
              />
            </Pressable>
          </>
        )}
      </ThemedView>

      <DiaryModal
        key={`diary-${diaryVisible}-${dashboard?.today?.activeMinutes ?? 0}`}
        visible={diaryVisible}
        onClose={() => setDiaryVisible(false)}
        petName={petName}
        petId={pet?.id ?? null}
        petgifId={pet?.petgif_id ?? null}
        activeMinutes={dashboard?.today?.activeMinutes ?? 0}
        onShared={() => {
          void load();
          router.push('/community');
        }}
      />
    </ScrollView>
  );
}

function VitalCell({
  icon,
  color,
  label,
  value,
  unit,
}: {
  icon: SymbolName;
  color: string;
  label: string;
  value: string;
  unit: string;
}) {
  return (
    <View style={styles.vitalCell}>
      <View style={styles.vitalHead}>
        <View style={[styles.vitalIconDot, { backgroundColor: `${color}14` }]}>
          <SymbolView name={icon} size={13} tintColor={color} />
        </View>
        <ThemedText type="small" themeColor="textSecondary" style={styles.vitalLabel}>
          {label}
        </ThemedText>
      </View>
      <View style={styles.vitalValueRow}>
        <ThemedText style={[styles.vitalValue, { fontFamily: Fonts.rounded }]}>{value}</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">
          {unit}
        </ThemedText>
      </View>
    </View>
  );
}

function MiniPill({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  return (
    <View style={[styles.miniPill, { backgroundColor: theme.backgroundSelected }]}>
      <ThemedText type="small" themeColor="textSecondary" style={styles.miniPillLabel}>
        {label}
      </ThemedText>
      <ThemedText type="smallBold" style={styles.miniPillValue}>
        {value}
      </ThemedText>
    </View>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  return (
    <View style={[styles.summaryCell, { backgroundColor: theme.backgroundSelected }]}>
      <ThemedText type="small" themeColor="textSecondary" style={styles.summaryCellLabel}>
        {label}
      </ThemedText>
      <ThemedText type="smallBold" style={styles.summaryCellValue}>
        {value}
      </ThemedText>
    </View>
  );
}

function FloatingChip({
  icon,
  iconColor,
  value,
  label,
  style,
}: {
  icon: SymbolName;
  iconColor: string;
  value: string;
  label: string;
  style?: StyleProp<ViewStyle>;
}) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.floatingChip,
        { backgroundColor: theme.backgroundElement, boxShadow: Shadows.chip },
        style,
      ]}
    >
      <SymbolView name={icon} size={18} tintColor={iconColor} />
      <ThemedText style={[styles.floatingChipValue, { fontFamily: Fonts.rounded }]}>
        {value}
      </ThemedText>
      <ThemedText type="small" themeColor="textSecondary" style={styles.floatingChipLabel}>
        {label}
      </ThemedText>
    </View>
  );
}

function GuideCard({
  emoji,
  title,
  text,
  buttonLabel,
  onPress,
}: {
  emoji: string;
  title: string;
  text: string;
  buttonLabel: string;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.guideCard,
        { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
      ]}
    >
      <ThemedText style={styles.guideEmoji}>{emoji}</ThemedText>
      <ThemedText type="smallBold">{title}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary" style={styles.guideText}>
        {text}
      </ThemedText>
      <Pressable
        onPress={onPress}
        style={({ pressed }) => [
          styles.guideButton,
          { backgroundColor: theme.tint, boxShadow: Shadows.chip },
          pressed && { opacity: 0.85 },
        ]}
      >
        <ThemedText type="smallBold" style={{ color: '#ffffff' }}>
          {buttonLabel}
        </ThemedText>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  scrollView: { flex: 1 },
  contentContainer: { flexDirection: 'row', justifyContent: 'center' },
  container: {
    flexGrow: 1,
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    paddingBottom: BottomTabInset + 70,
    gap: 12,
  },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  greeting: {
    fontSize: 28,
    fontWeight: '700',
    lineHeight: 36,
    textAlign: 'center',
  },
  greetingSub: { textAlign: 'center', marginTop: 2 },
  center: { paddingVertical: Spacing.six, alignItems: 'center' },

  petSwitcher: { gap: Spacing.two },
  petChip: {
    borderRadius: 16,
    paddingHorizontal: Spacing.three,
    paddingVertical: 6,
  },

  hero: {
    alignItems: 'center',
    justifyContent: 'center',
    height: 210,
  },
  avatarRing: {
    width: 174,
    height: 174,
    borderRadius: 87,
    borderWidth: 3,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarInner: {
    width: 162,
    height: 162,
    borderRadius: 81,
    borderWidth: 6,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  avatarGif: { width: '100%', height: '100%' },
  avatarEmoji: { fontSize: 56, lineHeight: 68, marginTop: 4 },
  onlineBadge: {
    position: 'absolute',
    bottom: 2,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 5,
  },
  onlineDot: { width: 7, height: 7, borderRadius: 4 },
  onlineText: { fontSize: 12, lineHeight: 16 },

  floatingChip: {
    position: 'absolute',
    alignItems: 'center',
    gap: 2,
    borderRadius: 18,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two + 2,
    minWidth: 76,
    zIndex: 2,
  },
  chipLeft: { left: 6, top: 28 },
  chipRight: { right: 6, top: 58 },
  floatingChipValue: { fontSize: 19, fontWeight: '800', lineHeight: 24 },
  floatingChipLabel: { fontSize: 11, lineHeight: 14 },

  vitalsCard: {
    borderRadius: 22,
    padding: 16,
    gap: 12,
  },
  sectionHead: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  sectionSummary: { fontSize: 12, lineHeight: 17 },
  vitalsTitle: { fontSize: 18, fontWeight: '700', lineHeight: 24 },
  livePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  liveDot: { width: 6, height: 6, borderRadius: 3 },
  vitalsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  vitalCell: {
    flexGrow: 1,
    flexBasis: '31%',
    gap: 5,
  },
  vitalHead: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  vitalIconDot: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  vitalLabel: { fontSize: 12, lineHeight: 16 },
  vitalValueRow: { flexDirection: 'row', alignItems: 'baseline', gap: 3, paddingLeft: 2 },
  vitalValue: { fontSize: 24, fontWeight: '800', lineHeight: 30 },
  miniRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: 12,
    paddingRight: 68,
  },
  miniPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 6,
    flexGrow: 1,
  },
  miniPillLabel: { fontSize: 12, lineHeight: 16 },
  miniPillValue: { fontSize: 13, lineHeight: 17 },

  trendCard: {
    borderRadius: 22,
    padding: 14,
    gap: 10,
  },
  trendHeadRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: Spacing.two,
  },
  trendTitle: { fontSize: 18, fontWeight: '700', lineHeight: 24 },
  trendSwitch: {
    flexDirection: 'row',
    gap: 6,
  },
  trendChip: {
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 4,
    backgroundColor: 'rgba(148,131,111,0.12)',
  },
  trendChipText: { fontSize: 11, lineHeight: 15 },
  periodRow: { flexDirection: 'row', gap: 6 },
  periodChip: {
    borderRadius: 12,
    paddingHorizontal: 10,
    paddingVertical: 4,
    backgroundColor: 'rgba(148,131,111,0.12)',
  },
  trendChart: { width: '100%' },
  trendLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  trendLabelText: { fontSize: 10, lineHeight: 13 },
  summaryRow: {
    flexDirection: 'row',
    gap: 8,
  },
  summaryCell: {
    flex: 1,
    alignItems: 'center',
    borderRadius: 12,
    paddingVertical: 8,
    gap: 2,
  },
  summaryCellLabel: { fontSize: 11, lineHeight: 14 },
  summaryCellValue: { fontSize: 15, lineHeight: 19 },

  diaryEntry: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    borderRadius: 20,
    padding: Spacing.three,
  },
  diaryTexts: { flex: 1, gap: 2 },

  guideCard: {
    borderRadius: 24,
    padding: Spacing.five,
    alignItems: 'center',
    gap: Spacing.two,
  },
  guideEmoji: { fontSize: 44 },
  guideText: { textAlign: 'center' },
  guideButton: {
    marginTop: Spacing.two,
    borderRadius: 16,
    paddingHorizontal: Spacing.five,
    paddingVertical: 10,
  },
});
