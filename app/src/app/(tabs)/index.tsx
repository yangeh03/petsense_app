import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
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
import { BottomTabInset, MaxContentWidth, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useObservations } from '@/hooks/use-observations';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import { clockTime, currentPrediction, duration } from '@/services/observation-view';
import { parseTelemetryMessage } from '@/services/telemetry-service';
import type { ObservationPeriod } from '@/types/observations';

export default function DashboardScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const { latest: live, displayState, isFresh, now } = useTelemetry();
  const [period, setPeriod] = useState<ObservationPeriod>('day');
  const {
    dashboard,
    observations: data,
    busy,
    error,
    reload,
  } = useObservations(period, displayState !== 'replaying');
  const [pullRefreshing, setPullRefreshing] = useState(false);
  const [vitalsOpen, setVitalsOpen] = useState(false);
  const [diaryVisible, setDiaryVisible] = useState(false);
  const pet = dashboard?.pet ?? null;
  const snapshot = data?.latest
    ? parseTelemetryMessage(
        JSON.stringify({
          topic: 'petsense/sensor/edge_v1',
          received_at: data.latest.receivedAt,
          payload: data.latest.payload,
        }),
      )
    : null;
  const replay = displayState === 'replaying';
  const matchingLive = pet?.device_id && live?.deviceId === pet.device_id && isFresh ? live : null;
  const matchingSnapshot = pet?.device_id && snapshot?.deviceId === pet.device_id ? snapshot : null;
  const frame = matchingLive ?? matchingSnapshot;
  const age = frame ? now - Date.parse(frame.receivedAt) : Infinity;
  const current = !!frame && age >= -5000 && age < 10_000 && !replay;
  const health = current && frame?.health?.valid ? frame.health : null;
  const prediction = currentPrediction(frame, current);
  const battery = frame?.battery?.found ? frame.battery.percent : null;
  const summary = data?.summary;
  const hasCoverage = !!summary?.firstObservedAt;
  const statusLabel = !pet?.device_id
    ? '等待绑定项圈'
    : replay
      ? '正在回放实验数据'
      : matchingLive && current
        ? '项圈在线'
        : current
          ? `最近同步 ${clockTime(frame!.receivedAt)}`
          : matchingSnapshot
            ? `最近更新 ${clockTime(matchingSnapshot.receivedAt)}`
            : '等待项圈数据';
  const color = current ? theme.online : theme.textSecondary;
  const avatar = pet?.avatarImageUrl ? absoluteUrl(pet.avatarImageUrl) : null;
  const format = (value: number | undefined, decimals = 0) =>
    value !== undefined && Number.isFinite(value) && value > 0 ? value.toFixed(decimals) : '—';

  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.scroll}
      refreshControl={
        <RefreshControl
          refreshing={pullRefreshing}
          onRefresh={() => {
            setPullRefreshing(true);
            void reload().finally(() => setPullRefreshing(false));
          }}
          tintColor={theme.tint}
        />
      }
    >
      <View
        style={[
          styles.container,
          { paddingTop: insets.top + 16, paddingBottom: insets.bottom + BottomTabInset + 110 },
        ]}
      >
        <View style={styles.heading}>
          <View style={styles.titleGroup}>
            <ThemedText type="small" themeColor="textSecondary">
              {new Date(now).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })} · 今日
            </ThemedText>
            <ThemedText style={styles.pageTitle}>
              {replay ? '实验数据回放' : pet ? `${pet.name}的今天` : '和它一起，好好生活'}
            </ThemedText>
          </View>
          {pet && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="编辑宠物档案"
              onPress={() => router.push('/pet-profile')}
              style={[styles.avatar, { backgroundColor: theme.tintBackground }]}
            >
              {avatar ? (
                <Image source={{ uri: avatar }} style={styles.avatarImage} />
              ) : (
                <ThemedText style={styles.avatarEmoji}>{pet.avatar_emoji}</ThemedText>
              )}
            </Pressable>
          )}
        </View>
        {error ? (
          <Pressable
            accessibilityRole="button"
            onPress={() => void reload()}
            style={[styles.notice, { backgroundColor: theme.tintBackground }]}
          >
            <ThemedText type="small">{error}</ThemedText>
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              重新加载
            </ThemedText>
          </Pressable>
        ) : null}
        {replay ? (
          <>
            <View style={[styles.card, { backgroundColor: theme.tintBackground }]}>
              <ThemedText style={styles.sectionTitle}>真实实验数据 · 循环回放</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                这是已录制的实验数据，不代表项圈当前状态，也不会写入今日记录。
              </ThemedText>
            </View>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText style={styles.sectionTitle}>实验中的读数</ThemedText>
              <View style={styles.metrics}>
                <Metric
                  label="心率"
                  value={format(live?.health?.valid ? live.health.heartRate : undefined)}
                  unit="bpm"
                />
                <Metric
                  label="血氧"
                  value={format(live?.health?.valid ? live.health.spo2 : undefined)}
                  unit="%"
                />
                <Metric
                  label="传感器温度"
                  value={format(live?.health?.valid ? live.health.bodyTemp : undefined, 1)}
                  unit="℃"
                />
              </View>
              <ThemedText type="small" themeColor="textSecondary">
                此回放片段未包含端侧声音与动作分类，不补造这些结果。
              </ThemedText>
            </View>
          </>
        ) : authLoading || (user && !dashboard && busy) ? (
          <ActivityIndicator color={theme.tint} />
        ) : !user ? (
          <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText style={styles.sectionTitle}>从认识你的宠物开始</ThemedText>
            <ThemedText themeColor="textSecondary">
              登录后连接项圈，查看它的状态和每天的记录。
            </ThemedText>
            <Pressable
              accessibilityRole="button"
              onPress={() => router.push('/login')}
              style={[styles.button, { backgroundColor: theme.tintBackground }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.tint }}>
                登录 / 注册
              </ThemedText>
            </Pressable>
          </View>
        ) : dashboard && !pet ? (
          <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText style={styles.sectionTitle}>先建立一份宠物档案</ThemedText>
            <ThemedText themeColor="textSecondary">
              添加名字、连接项圈后，这里会显示真实观测。
            </ThemedText>
            <Pressable
              accessibilityRole="button"
              onPress={() => router.push('/setup')}
              style={[styles.button, { backgroundColor: theme.tintBackground }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.tint }}>
                建立档案与连接项圈
              </ThemedText>
            </Pressable>
          </View>
        ) : pet ? (
          <>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="查看项圈连接与诊断"
              onPress={() => router.push(pet.device_id ? '/monitor' : '/setup')}
              style={styles.status}
            >
              <View style={[styles.dot, { backgroundColor: color }]} />
              <ThemedText type="small" style={[styles.statusText, { color }]}>
                {statusLabel}
              </ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {battery !== null ? `${Math.round(battery)}% · ` : ''}详情 ›
              </ThemedText>
            </Pressable>
            <View style={[styles.nowCard, { backgroundColor: theme.tintBackground }]}>
              <View style={styles.rowBetween}>
                <ThemedText type="smallBold" style={{ color: theme.tint }}>
                  {current ? '此刻的它' : '项圈状态'}
                </ThemedText>
                {current && (
                  <ThemedText type="small" themeColor="textSecondary">
                    项圈识别
                  </ThemedText>
                )}
              </View>
              <ThemedText style={current ? styles.currentTitle : styles.sectionTitle}>
                {current
                  ? prediction.action
                  : replay
                    ? '正在回放实验数据'
                    : frame
                      ? '项圈暂未在线'
                      : '等待首次观测'}
              </ThemedText>
              {current && (
                <View style={styles.soundRow}>
                  <SymbolView
                    name={{ ios: 'waveform', android: 'graphic_eq', web: 'graphic_eq' }}
                    size={18}
                    tintColor={theme.tint}
                  />
                  <ThemedText>
                    {prediction.sound}
                    {prediction.experimental ? ' · 实验性识别' : ''}
                  </ThemedText>
                </View>
              )}
              {!current && (
                <ThemedText type="small" themeColor="textSecondary">
                  {replay
                    ? '回放数据不会混入宠物的实时状态和今日统计。'
                    : '恢复连接后自动更新，今天的记录仍可查看。'}
                </ThemedText>
              )}
            </View>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <View style={styles.rowBetween}>
                <ThemedText style={styles.sectionTitle}>最近读数</ThemedText>
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ expanded: vitalsOpen }}
                  onPress={() => setVitalsOpen(!vitalsOpen)}
                  style={styles.tap}
                >
                  <ThemedText type="small" style={{ color: theme.tint }}>
                    {vitalsOpen ? '收起说明' : '测量说明'}
                  </ThemedText>
                </Pressable>
              </View>
              {current ? (
                <View style={styles.metrics}>
                  <Metric label="心率" value={format(health?.heartRate)} unit="bpm" />
                  <Metric label="血氧" value={format(health?.spo2)} unit="%" />
                  <Metric label="传感器温度" value={format(health?.bodyTemp, 1)} unit="℃" />
                </View>
              ) : (
                <ThemedText type="small" themeColor="textSecondary">
                  暂无实时读数，恢复连接后自动更新。
                </ThemedText>
              )}
              {current && !health && (
                <ThemedText type="small" themeColor="textSecondary">
                  暂未获得有效读数，请确认传感器佩戴与接触情况。
                </ThemedText>
              )}
              {health && health.bodyTemp >= 42 && (
                <ThemedText type="small" style={{ color: theme.warning }}>
                  温度读数偏高，请先核对佩戴和传感器状态。
                </ThemedText>
              )}
              {vitalsOpen && (
                <View style={[styles.explanation, { backgroundColor: theme.background }]}>
                  <ThemedText type="small" themeColor="textSecondary">
                    温度是项圈传感器上报值，不能独立判断宠物是否发热。无效、未更新的读数不会作为当前值显示。
                  </ThemedText>
                  <ThemedText type="small" themeColor="textSecondary">
                    呼吸、HRV和环境温度等参数可在设备诊断中查看。
                  </ThemedText>
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => router.push('/monitor')}
                    style={styles.tap}
                  >
                    <ThemedText type="smallBold" style={{ color: theme.tint }}>
                      查看设备诊断 ›
                    </ThemedText>
                  </Pressable>
                </View>
              )}
            </View>
            <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <View style={styles.rowBetween}>
                <ThemedText style={styles.sectionTitle}>今天发生了什么</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  {hasCoverage ? `已观测${duration(summary!.observedSeconds)}` : '尚未开始观测'}
                </ThemedText>
              </View>
              <View style={[styles.summary, { backgroundColor: theme.background }]}>
                <Metric
                  label="走动"
                  value={hasCoverage ? duration(summary!.walkingSeconds) : '—'}
                  compact
                />
                <Metric
                  label="静止"
                  value={hasCoverage ? duration(summary!.stationarySeconds) : '—'}
                  compact
                />
                <Metric
                  label="叫声片段"
                  value={hasCoverage ? `${summary!.soundEpisodeCount}段` : '—'}
                  compact
                />
              </View>
              <ThemedText type="small" themeColor="textSecondary">
                仅统计实际观测时段；静止不等于睡眠，片段不等于单声次数。
              </ThemedText>
              <SoundTimeline events={data?.events ?? []} hasCoverage={hasCoverage} />
              {data && (
                <ThemedText type="small" themeColor="textSecondary">
                  统计更新于 {clockTime(data.range.endAt)}
                </ThemedText>
              )}
            </View>
            <TrendPanel data={data} period={period} onPeriod={setPeriod} busy={busy} />
            {pet.petgif_id && (
              <Pressable
                accessibilityRole="button"
                onPress={() => setDiaryVisible(true)}
                style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              >
                <ThemedText type="smallBold">制作运动日记 ›</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  用今天真实的行走记录，留下和它的回忆。
                </ThemedText>
              </Pressable>
            )}
            <ThemedText type="small" themeColor="textSecondary" style={styles.footer}>
              识别结果是观测记录，不替代对宠物状态的实际观察。
            </ThemedText>
          </>
        ) : (
          !busy && (
            <Pressable accessibilityRole="button" onPress={() => void reload()} style={styles.card}>
              <ThemedText>暂时无法加载宠物档案，点击重试。</ThemedText>
            </Pressable>
          )
        )}
      </View>
      {pet && (
        <DiaryModal
          visible={diaryVisible}
          onShared={() => {
            setDiaryVisible(false);
            void reload();
          }}
          onClose={() => setDiaryVisible(false)}
          petName={pet.name}
          petId={pet.id}
          petgifId={pet.petgif_id ?? null}
          activeMinutes={Math.floor((summary?.walkingSeconds ?? 0) / 60)}
        />
      )}
    </ScrollView>
  );
}

function Metric({
  label,
  value,
  unit,
  compact = false,
}: {
  label: string;
  value: string;
  unit?: string;
  compact?: boolean;
}) {
  const theme = useTheme();
  return (
    <View style={styles.metric}>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
      <ThemedText
        style={[compact ? styles.compactValue : styles.metricValue, { color: theme.text }]}
      >
        {value}
      </ThemedText>
      {unit && (
        <ThemedText type="small" themeColor="textSecondary">
          {unit}
        </ThemedText>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  scroll: { alignItems: 'center' },
  container: { width: '100%', maxWidth: MaxContentWidth, paddingHorizontal: Spacing.four, gap: 18 },
  heading: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  titleGroup: { flex: 1, gap: 8 },
  pageTitle: { fontSize: 28, lineHeight: 36, fontWeight: '700' },
  avatar: {
    width: 56,
    height: 56,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  avatarImage: { width: '100%', height: '100%' },
  avatarEmoji: { fontSize: 30, lineHeight: 38 },
  status: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 4 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  statusText: { flex: 1 },
  card: { padding: 20, borderRadius: 22, gap: 16 },
  nowCard: { padding: 24, borderRadius: 26, gap: 14 },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: 8,
  },
  currentTitle: { fontSize: 34, lineHeight: 44, fontWeight: '700' },
  soundRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  metrics: { flexDirection: 'row', gap: 12 },
  metric: { flex: 1, gap: 5 },
  metricValue: { fontSize: 28, lineHeight: 36, fontWeight: '700', fontVariant: ['tabular-nums'] },
  compactValue: { fontSize: 16, lineHeight: 23, fontWeight: '600' },
  summary: { flexDirection: 'row', padding: 14, borderRadius: 14, gap: 10 },
  explanation: { padding: 14, borderRadius: 14, gap: 10 },
  button: { alignSelf: 'flex-start', paddingHorizontal: 18, paddingVertical: 12, borderRadius: 14 },
  notice: { padding: 16, borderRadius: 16, gap: 8 },
  tap: { paddingVertical: 6 },
  footer: { textAlign: 'center', paddingHorizontal: 12 },
});
