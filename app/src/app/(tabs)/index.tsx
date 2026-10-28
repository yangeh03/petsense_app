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

import { ActivityCard } from '@/components/dashboard/activity-card';
import { NotificationBell } from '@/components/alerts/notification-bell';
import { PetHero } from '@/components/dashboard/pet-hero';
import { PetStatusStrip } from '@/components/dashboard/pet-status-strip';
import { VitalsCard } from '@/components/dashboard/vitals-card';
import { ThemedText } from '@/components/themed-text';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useObservations } from '@/hooks/use-observations';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';
import { clockTime } from '@/services/observation-view';
import { parseTelemetryMessage } from '@/services/telemetry-service';

export default function DashboardScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const { latest: live, displayState, isFresh, now, series } = useTelemetry();
  const {
    dashboard,
    observations: data,
    busy,
    error,
    reload,
  } = useObservations('day', displayState !== 'replaying');
  const [pullRefreshing, setPullRefreshing] = useState(false);
  const [mediaRefresh, setMediaRefresh] = useState(0);
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
  const battery = frame?.battery?.found ? Math.round(frame.battery.percent) : null;
  const summary = data?.summary;
  const hasCoverage = !!summary?.firstObservedAt;
  const online = !!matchingLive && current;
  const statusLabel = replay
    ? '正在回放实验数据'
    : online
      ? '项圈在线'
      : current
        ? `最近同步 ${clockTime(frame!.receivedAt)}`
        : matchingSnapshot
          ? `最近更新 ${clockTime(matchingSnapshot.receivedAt)}`
          : '等待项圈数据';
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
            setMediaRefresh((version) => version + 1);
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
          <ThemedText type="small" themeColor="textSecondary">
            {new Date(now).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })}
          </ThemedText>
          <NotificationBell />
        </View>
        <View style={styles.welcome}>
          <ThemedText style={[styles.pageTitle, { fontFamily: Fonts.rounded }]}>
            {replay
              ? '实验数据回放'
              : pet
                ? `${greeting(now)}，${pet.name}！`
                : '和它一起，好好生活'}
          </ThemedText>
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
                录制数据，不计入今日记录。
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
            </View>
          </>
        ) : authLoading || (user && !dashboard && busy) ? (
          <ActivityIndicator color={theme.tint} />
        ) : !user ? (
          <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText style={styles.sectionTitle}>从认识你的宠物开始</ThemedText>
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
            <PetHero
              key={`${pet.id}:${pet.petgif_id ?? ''}:${pet.avatar_template_id ?? ''}`}
              pet={pet}
              petgif={dashboard?.petgif}
              avatarTemplate={dashboard?.avatarTemplate}
              frame={frame}
              current={current}
              statusLabel={
                !pet.device_id ? '等待绑定项圈' : frame ? '项圈暂未在线' : '等待首次观测'
              }
              refreshVersion={mediaRefresh}
              onEdit={() => router.push('/pet')}
            />
            <PetStatusStrip
              frame={frame}
              current={current}
              bound={!!pet.device_id}
              online={online}
              statusLabel={statusLabel}
              battery={battery}
              onPressCollar={() => router.push(pet.device_id ? '/profile' : '/setup')}
              onPressSound={() => router.push('/records')}
            />
            <VitalsCard
              health={health}
              current={current}
              daily={data?.metrics ?? null}
              series={series}
              buckets={data?.buckets ?? []}
              updatedAt={frame?.receivedAt ?? null}
              onOpenCollar={() => router.push('/profile')}
            />
            <ActivityCard
              summary={summary ?? null}
              timeline={data?.timeline}
              hasCoverage={hasCoverage}
              onPress={() => router.push('/records')}
            />
            <Pressable
              accessibilityRole="button"
              onPress={() => router.push('/records')}
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
            >
              <ThemedText type="smallBold">
                {pet.petgif_id ? '制作运动日记 ›' : '查看运动日记条件 ›'}
              </ThemedText>
              {!pet.petgif_id && (
                <ThemedText type="small" themeColor="textSecondary">
                  预设动画仅用于首页展示；需要个人 PetGIF 数字形象才能生成日记。
                </ThemedText>
              )}
            </Pressable>
          </>
        ) : (
          !busy && (
            <Pressable accessibilityRole="button" onPress={() => void reload()} style={styles.card}>
              <ThemedText>暂时无法加载宠物档案，点击重试。</ThemedText>
            </Pressable>
          )
        )}
      </View>
    </ScrollView>
  );
}

function greeting(now: number) {
  const hour = new Date(now).getHours();
  if (hour < 5) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

function Metric({ label, value, unit }: { label: string; value: string; unit?: string }) {
  const theme = useTheme();
  return (
    <View style={styles.metric}>
      <ThemedText type="small" themeColor="textSecondary">
        {label}
      </ThemedText>
      <ThemedText style={[styles.metricValue, { color: theme.text }]}>{value}</ThemedText>
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
  container: { width: '100%', maxWidth: MaxContentWidth, paddingHorizontal: Spacing.four, gap: 12 },
  heading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  welcome: { alignItems: 'center', gap: 4 },
  pageTitle: { fontSize: 26, lineHeight: 34, fontWeight: '700', textAlign: 'center' },
  card: { padding: 16, borderRadius: 22, gap: 12, boxShadow: Shadows.card },
  sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  metrics: { flexDirection: 'row', gap: 12 },
  metric: { flex: 1, gap: 5 },
  metricValue: { fontSize: 28, lineHeight: 36, fontWeight: '700', fontVariant: ['tabular-nums'] },
  button: { alignSelf: 'flex-start', paddingHorizontal: 18, paddingVertical: 12, borderRadius: 14 },
  notice: { padding: 16, borderRadius: 16, gap: 8 },
});
