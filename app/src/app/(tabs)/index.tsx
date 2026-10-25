import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useRef, useState, type ComponentProps } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { DeviceStatusCard } from '@/components/dashboard/device-status-card';
import { selectDashboardReadings } from '@/services/dashboard-readings';
import { DiaryModal } from '@/components/dashboard/diary-modal';
import { NotificationBell } from '@/components/alerts/notification-bell';
import { TrendCard } from '@/components/dashboard/trend-card';
import { useTrends } from '@/hooks/use-trends';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAlerts, worstSeverity } from '@/hooks/use-alerts';
import { useAuth } from '@/hooks/use-auth';
import { usePetChanges } from '@/hooks/use-pet-changes';
import { useTelemetry } from '@/hooks/use-telemetry';
import { usePetAction } from '@/hooks/use-pet-action';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl, fetchDashboard } from '@/services/api';
import type { DashboardData, TrendMetric, TrendPeriod } from '@/types/social';

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
 * 今日页：设计稿风格（暖色圆角卡 / 柔和投影 / 圆体大字），
 * 内容为设备真实在测的体征遥测 + PetGIF 数字形象 + 时间维度统计趋势 + 运动日记。
 */
export default function DashboardScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const { activeAlerts } = useAlerts();
  const { latest, displayState } = useTelemetry();
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [diaryVisible, setDiaryVisible] = useState(false);
  // 板块主要体征始终可见，更多指标与历史趋势按需展开
  const [vitalsOpen, setVitalsOpen] = useState(false);
  const [trendOpen, setTrendOpen] = useState(true);
  const [trendMetric, setTrendMetric] = useState<TrendMetric>('heartRate');
  const [trendPeriod, setTrendPeriod] = useState<TrendPeriod>('day');
  const trends = useTrends(user?.id, dashboard?.pet?.device_id, trendPeriod, trendOpen);
  const loadSequence = useRef(0);
  const loadingRequest = useRef(false);
  const loadedUserId = useRef<number | null>(null);

  const load = useCallback(async () => {
    if (!user || loadingRequest.current) return;
    loadingRequest.current = true;
    const sequence = ++loadSequence.current;
    try {
      const data = await fetchDashboard();
      if (sequence !== loadSequence.current) return;
      setDashboard(data);
      setError('');
    } catch {
      if (sequence === loadSequence.current) setError('数据暂时加载失败，请检查网络后重试');
    } finally {
      if (sequence === loadSequence.current) {
        loadingRequest.current = false;
        setLoading(false);
      }
    }
  }, [user]);

  usePetChanges(
    useCallback(() => {
      loadSequence.current++;
      loadingRequest.current = false;
      void load();
    }, [load]),
  );

  useFocusEffect(
    useCallback(() => {
      if (user) {
        if (loadedUserId.current !== user.id) {
          loadedUserId.current = user.id;
          setDashboard(null);
          setLoading(true);
        }
        void load();
        const timer = setInterval(() => void load(), 15_000);
        return () => {
          clearInterval(timer);
          loadSequence.current += 1;
          loadingRequest.current = false;
        };
      } else {
        loadedUserId.current = null;
        setLoading(false);
        setDashboard(null);
      }
    }, [user, load]),
  );

  const pet = dashboard?.pet ?? null;
  const petName = pet?.name ?? '毛孩子';
  const liveForPet = latest && pet?.device_id && latest.deviceId === pet.device_id ? latest : null;
  const latestForAction = displayState === 'streaming' ? liveForPet : null;
  const readings = selectDashboardReadings({
    pet,
    snapshot: dashboard?.latest,
    frame: latest,
    displayState,
  });
  const { online, vitals, batteryPercent } = readings;
  const fmt = (value: number | undefined | null, digits = 0) =>
    value && value > 0 ? value.toFixed(digits) : '--';

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

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => {
            setRefreshing(true);
            void Promise.all([load(), trends.refresh()]).finally(() => setRefreshing(false));
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
          {pet ? '今天也一起好好照顾它 🐾' : '从一份宠物档案，开启健康记录'}
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
        ) : !dashboard && error ? (
          <GuideCard
            emoji="↻"
            title="暂时无法读取档案"
            text={error}
            buttonLabel="重新加载"
            onPress={() => void load()}
          />
        ) : !pet ? (
          <GuideCard
            emoji="🐶"
            title="先认识你的毛孩子"
            text="1 建立宠物档案 → 2 输入项圈编码 → 3 查看健康数据。先起个名字，绑定可以稍后完成。"
            buttonLabel="添加宠物"
            onPress={() => router.push('/setup')}
          />
        ) : (
          <>
            {error ? (
              <ThemedText type="small" style={{ color: theme.danger }}>
                {error}，可下拉刷新
              </ThemedText>
            ) : null}
            <DeviceStatusCard
              state={readings.state}
              updatedAt={readings.updatedAt}
              batteryPercent={batteryPercent}
            />
            {/* 宠物头像与设备在线状态 */}
            <View style={styles.hero}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`查看 ${petName} 的宠物资料`}
                onPress={() => router.push('/pet')}
                style={[
                  styles.avatarRing,
                  { borderColor: ringColor, backgroundColor: theme.tintBackground },
                ]}
              >
                <View style={[styles.avatarInner, { borderColor: theme.backgroundElement }]}>
                  {heroAvatarImage ? (
                    <Image
                      source={{ uri: heroAvatarImage }}
                      style={styles.avatarGif}
                      contentFit="cover"
                    />
                  ) : heroGifUri ? (
                    <Image
                      source={{ uri: heroGifUri }}
                      style={styles.avatarGif}
                      contentFit="cover"
                      autoplay={true}
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
                    {!pet.device_id
                      ? '等待绑定项圈'
                      : latestForAction
                        ? `在线 · ${ACTION_LABELS[action]}`
                        : online
                          ? '项圈在线'
                          : '等待设备数据'}
                  </ThemedText>
                </View>
              </Pressable>
            </View>
            <Pressable
              accessibilityRole="button"
              onPress={() => router.push('/pet')}
              style={{ alignSelf: 'center', padding: Spacing.two }}
            >
              <ThemedText type="small" style={{ color: theme.tint }}>
                宠物资料 ›
              </ThemedText>
            </Pressable>

            {/* 三项主要体征始终可见，次要指标点标题展开。 */}
            <View
              style={[
                styles.vitalsCard,
                { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
              ]}
            >
              <Pressable
                style={styles.sectionHead}
                onPress={() => setVitalsOpen((value) => !value)}
                accessibilityLabel={vitalsOpen ? '收起更多指标' : '展开更多指标'}
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
                    {readings.state === 'live'
                      ? '实时'
                      : readings.state === 'snapshot'
                        ? '最近采样'
                        : '待测量'}
                  </ThemedText>
                </View>
                <SymbolView
                  name={{ ios: 'chevron.down', android: 'expand_more', web: 'expand_more' }}
                  size={15}
                  tintColor={theme.textSecondary}
                  style={{ transform: [{ rotate: vitalsOpen ? '180deg' : '0deg' }] }}
                />
              </Pressable>

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
              </View>
              {vitalsOpen && (
                <>
                  <View style={styles.vitalsGrid}>
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
              )}
            </View>

            <TrendCard
              key={`${user?.id}/${pet.id}/${pet.device_id}`}
              {...trends}
              expanded={trendOpen}
              setExpanded={setTrendOpen}
              period={trendPeriod}
              setPeriod={setTrendPeriod}
              metric={trendMetric}
              setMetric={setTrendMetric}
            />

            {/* 运动日记入口 */}
            {pet.petgif_id && (
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
            )}
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
    flex: 1,
    minWidth: 0,
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

  hero: {
    alignItems: 'center',
    justifyContent: 'center',
    height: 155,
  },
  avatarRing: {
    width: 132,
    height: 132,
    borderRadius: 66,
    borderWidth: 3,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarInner: {
    width: 120,
    height: 120,
    borderRadius: 60,
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
