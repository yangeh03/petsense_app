import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import type { ComponentProps } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { MapViewport } from '@/components/location/map-viewport';
import { GpsMap } from '@/components/location/gps-map';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useLocationMode } from '@/hooks/use-location-mode';
import { useObservations } from '@/hooks/use-observations';
import { usePositionMap } from '@/hooks/use-position-map';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';
import { gpsView } from '@/services/gps-view';
import { parseTelemetryMessage } from '@/services/telemetry-service';

import type { LocationMode } from '@/services/location-mode';

/** 室内地图不再绘制实时轨迹；MapViewport 仅绘制当前（或最后）位置。 */
const EMPTY_TRAIL: { x: number; y: number }[] = [];

function formatClock(timestamp: number) {
  const date = new Date(timestamp);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

/** Coordinates require both real UWB measurements and a calibrated layout; never substitute a demo route. */
export default function TrackingScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { dashboard, observations } = useObservations('day');
  const { latest, isFresh, now, displayState } = useTelemetry();
  const snapshot = useMemo(
    () =>
      observations?.latest
        ? parseTelemetryMessage(
            JSON.stringify({
              topic: 'petsense/sensor/snapshot',
              payload: observations.latest.payload,
              received_at: observations.latest.receivedAt,
            }),
          )
        : null,
    [observations],
  );
  const gps = gpsView(
    dashboard?.pet?.device_id,
    latest,
    snapshot,
    now,
    displayState === 'replaying',
  );
  const matching = latest?.deviceId === dashboard?.pet?.device_id && isFresh ? latest : null;
  const hasRanges = matching?.uwb?.valid && matching.uwb.distanceMm.length > 0;
  const location = usePositionMap(dashboard?.pet?.device_id);
  const [dragging, setDragging] = useState(false);
  const { mode, setMode } = useLocationMode();
  const deviceId = dashboard?.pet?.device_id ?? null;
  const [showGpsDetails, setShowGpsDetails] = useState(false);
  // 失联后保留的最后一个位置：用服务端给的失效时间标注「最后位置」。
  const lastFreshUntil = (() => {
    const expiresAt = location.position?.expiresAt;
    if (!expiresAt) return null;
    const parsed = Date.parse(expiresAt);
    return Number.isFinite(parsed) ? parsed : null;
  })();
  const indoorStatus = (() => {
    if (!deviceId) return { label: '未绑定', live: false };
    if (location.map && location.fresh) return { label: '实时定位', live: true };
    if (location.map && location.position)
      return {
        label: lastFreshUntil ? `最后位置 · 至 ${formatClock(lastFreshUntil)}` : '最后位置',
        live: false,
      };
    if (!location.map && location.error !== '' && !location.loading)
      return { label: '更新失败', live: false };
    if (location.loading) return { label: '正在获取地图…', live: false };
    return { label: '未标定', live: false };
  })();
  const outdoorStatus = (() => {
    if (gps.point) return { label: '实时定位', live: true };
    if (gps.status === '请先绑定项圈') return { label: '未绑定', live: false };
    if (gps.status === 'GPS 定位已过期') return { label: '定位已过期', live: false };
    if (displayState === 'replaying') return { label: '回放模式', live: false };
    return { label: '等待定位', live: false };
  })();
  return (
    <ScrollView
      scrollEnabled={!dragging}
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.scroll}
    >
      <View
        style={[
          styles.container,
          {
            paddingTop: insets.top + Spacing.four,
            paddingBottom: BottomTabInset + insets.bottom + 28,
          },
        ]}
      >
        <ThemedText style={styles.title}>位置</ThemedText>
        <ModeSegment mode={mode} onChange={setMode} />
        {mode === 'indoor' ? (
          <View style={styles.panel}>
            <View style={styles.sectionHead}>
              <View style={styles.sectionHeadText}>
                <ThemedText style={styles.sectionTitle}>室内地图</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  {location.map?.name ?? '室内基站定位'}
                </ThemedText>
              </View>
              <StatusChip label={indoorStatus.label} live={indoorStatus.live} />
            </View>
            {location.map && (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <MapViewport
                  key={`${location.map.mapId}:${location.map.version}:${location.map.revision ?? ''}`}
                  map={location.map}
                  position={location.position}
                  trail={EMPTY_TRAIL}
                  fresh={location.fresh}
                  onDragging={setDragging}
                  onRetry={location.reload}
                />
              </View>
            )}
            {location.loading && !location.map && (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <ActivityIndicator color={theme.tint} />
                <ThemedText type="small" themeColor="textSecondary">
                  正在获取地图…
                </ThemedText>
              </View>
            )}
            {!location.map && !location.loading && (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <View style={[styles.icon, { backgroundColor: theme.tintBackground }]}>
                  <SymbolView
                    name={
                      deviceId
                        ? { ios: 'location.slash', android: 'location_off', web: 'location_off' }
                        : { ios: 'pawprint.fill', android: 'pets', web: 'pets' }
                    }
                    size={32}
                    tintColor={theme.tint}
                  />
                </View>
                <ThemedText style={styles.cardTitle}>
                  {!deviceId
                    ? '先绑定项圈'
                    : hasRanges
                      ? '已收到测距，尚未完成户型标定'
                      : '等待室内定位数据'}
                </ThemedText>
                <ThemedText themeColor="textSecondary" style={styles.description}>
                  {!deviceId
                    ? '绑定后可接收项圈的室内定位，并在这里标定室内地图。'
                    : hasRanges
                      ? '项圈提供了真实测距数据，但尚不能据此确定房间。完成锚点与户型标定后，才能显示位置。'
                      : '当前没有收到可用的 UWB 坐标。完成锚点与地图标定后，这里会显示室内位置。'}
                </ThemedText>
                {deviceId ? (
                  <>
                    <Pressable
                      accessibilityRole="button"
                      onPress={() => router.push('/uwb-setup')}
                      style={[styles.button, { backgroundColor: theme.tintBackground }]}
                    >
                      <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                        开始标定 ›
                      </ThemedText>
                    </Pressable>
                    <Pressable accessibilityRole="button" onPress={() => router.push('/monitor')}>
                      <ThemedText type="small" themeColor="textSecondary">
                        查看项圈能力 ›
                      </ThemedText>
                    </Pressable>
                  </>
                ) : (
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => router.push('/setup')}
                    style={[styles.button, { backgroundColor: theme.tintBackground }]}
                  >
                    <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                      去绑定项圈 ›
                    </ThemedText>
                  </Pressable>
                )}
              </View>
            )}
            {deviceId && (
              <LocationRow
                icon={{ ios: 'gearshape', android: 'settings', web: 'settings' }}
                title="室内地图设置"
                subtitle="标定基站与更新地图"
                onPress={() => router.push('/uwb-setup')}
              />
            )}
            {location.error !== '' && (
              <Pressable accessibilityRole="button" onPress={location.reload}>
                <ThemedText type="small" themeColor="textSecondary">
                  位置暂未更新：{location.error} · 点击重试
                </ThemedText>
              </Pressable>
            )}
          </View>
        ) : (
          <View style={styles.panel}>
            <View style={styles.sectionHead}>
              <View style={styles.sectionHeadText}>
                <ThemedText style={styles.sectionTitle}>项圈位置</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  卫星定位 · WGS84
                </ThemedText>
              </View>
              <StatusChip label={outdoorStatus.label} live={outdoorStatus.live} />
            </View>
            {gps.point ? (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <GpsMap key={deviceId ?? 'gps'} point={gps.point} onDragging={setDragging} />
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ selected: showGpsDetails }}
                  onPress={() => setShowGpsDetails(!showGpsDetails)}
                >
                  <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                    {showGpsDetails ? '收起坐标详情' : '坐标详情'}
                  </ThemedText>
                </Pressable>
                {showGpsDetails && (
                  <View style={styles.details}>
                    <ThemedText type="small" themeColor="textSecondary">
                      纬度 {gps.point.latitude.toFixed(7)} · 经度 {gps.point.longitude.toFixed(7)}
                    </ThemedText>
                    <ThemedText type="small" themeColor="textSecondary">
                      定位时间（UTC）{gps.utc}
                    </ThemedText>
                  </View>
                )}
              </View>
            ) : (
              <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <View style={[styles.icon, { backgroundColor: theme.tintBackground }]}>
                  <SymbolView
                    name={{
                      ios: 'antenna.radiowaves.left.and.right',
                      android: 'satellite_alt',
                      web: 'satellite_alt',
                    }}
                    size={32}
                    tintColor={theme.tint}
                  />
                </View>
                <ThemedText style={styles.cardTitle}>{gps.status}</ThemedText>
                <ThemedText themeColor="textSecondary" style={styles.description}>
                  {!deviceId
                    ? '绑定后可在这里查看项圈位置。'
                    : gps.status === 'GPS 定位已过期'
                      ? '收到新的定位后，地图会恢复显示。'
                      : '到开阔处后，项圈的定位通常更容易更新。'}
                </ThemedText>
                {!deviceId && (
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => router.push('/setup')}
                    style={[styles.button, { backgroundColor: theme.tintBackground }]}
                  >
                    <ThemedText type="smallBold" style={{ color: theme.tintForeground }}>
                      去绑定项圈 ›
                    </ThemedText>
                  </Pressable>
                )}
              </View>
            )}
            <View style={[styles.divider, { borderTopColor: theme.backgroundSelected }]} />
            <LocationRow
              icon={{ ios: 'map.fill', android: 'map', web: 'map' }}
              title="遛狗地图"
              subtitle="广州南沙 · 已有路段评分"
              onPress={() => router.push('/dog-map')}
            />
          </View>
        )}
        <View style={[styles.info, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="smallBold">室内与室外定位独立</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            室内使用 UWB 测距与已标定地图，室外使用项圈
            GNSS（WGS84）。两路坐标互不混用，页面不会自动切换场景。
          </ThemedText>
        </View>
      </View>
    </ScrollView>
  );
}

function ModeSegment({
  mode,
  onChange,
}: {
  mode: LocationMode;
  onChange: (mode: LocationMode) => void;
}) {
  const theme = useTheme();
  const items = [
    { value: 'indoor', label: '室内', icon: { ios: 'house.fill', android: 'home', web: 'home' } },
    {
      value: 'outdoor',
      label: '室外',
      icon: { ios: 'tree.fill', android: 'forest', web: 'forest' },
    },
  ] as const;
  return (
    <View
      accessibilityRole="tablist"
      accessibilityLabel="定位场景"
      style={[styles.segment, { backgroundColor: theme.backgroundSelected }]}
    >
      {items.map((item) => {
        const selected = mode === item.value;
        return (
          <Pressable
            key={item.value}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            accessibilityLabel={item.label}
            onPress={() => onChange(item.value)}
            style={[styles.segmentItem, selected && { backgroundColor: theme.backgroundElement }]}
          >
            <SymbolView
              name={item.icon}
              size={15}
              tintColor={selected ? theme.tint : theme.textSecondary}
            />
            <ThemedText
              type="smallBold"
              style={{ color: selected ? theme.text : theme.textSecondary }}
            >
              {item.label}
            </ThemedText>
          </Pressable>
        );
      })}
    </View>
  );
}

function StatusChip({ label, live }: { label: string; live: boolean }) {
  const theme = useTheme();
  return (
    <View style={styles.chip}>
      <View
        style={[styles.chipDot, { backgroundColor: live ? theme.online : theme.textSecondary }]}
      />
      <ThemedText
        type="small"
        style={{ color: live ? theme.onlineForeground : theme.textSecondary }}
        numberOfLines={1}
      >
        {label}
      </ThemedText>
    </View>
  );
}

function LocationRow({
  icon,
  title,
  subtitle,
  onPress,
}: {
  icon: ComponentProps<typeof SymbolView>['name'];
  title: string;
  subtitle: string;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${title}，${subtitle}`}
      onPress={onPress}
      style={styles.row}
    >
      <View style={[styles.rowIcon, { backgroundColor: theme.tintBackground }]}>
        <SymbolView name={icon} size={17} tintColor={theme.tint} />
      </View>
      <View style={styles.rowText}>
        <ThemedText type="smallBold">{title}</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">
          {subtitle}
        </ThemedText>
      </View>
      <SymbolView
        name={{ ios: 'chevron.right', android: 'chevron_right', web: 'chevron_right' }}
        size={13}
        tintColor={theme.textSecondary}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  scroll: { alignItems: 'center' },
  container: {
    width: '100%',
    maxWidth: MaxContentWidth,
    paddingHorizontal: Spacing.four,
    gap: Spacing.four,
  },
  title: { fontFamily: Fonts.rounded, fontSize: 26, lineHeight: 34, fontWeight: '700' },
  segment: {
    flexDirection: 'row',
    borderRadius: 13,
    padding: 3,
    gap: 3,
  },
  segmentItem: {
    flex: 1,
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    borderRadius: 10,
  },
  panel: { gap: Spacing.three },
  sectionHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  sectionHeadText: { flex: 1, gap: 2 },
  sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    minWidth: 44,
    flexShrink: 1,
  },
  chipDot: { width: 7, height: 7, borderRadius: 4 },
  card: {
    borderRadius: 22,
    padding: Spacing.five,
    gap: Spacing.three,
    boxShadow: Shadows.card,
    alignItems: 'flex-start',
  },
  icon: { padding: 18, borderRadius: 22 },
  cardTitle: { fontSize: 21, lineHeight: 30, fontWeight: '700' },
  description: { lineHeight: 26 },
  button: { paddingHorizontal: 18, paddingVertical: 12, borderRadius: 16 },
  details: { gap: Spacing.one },
  divider: { borderTopWidth: 1, paddingTop: Spacing.two },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    minHeight: 66,
    paddingVertical: 8,
  },
  rowIcon: {
    width: 36,
    height: 36,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowText: { flex: 1, gap: 2 },
  info: { borderTopWidth: 1, paddingTop: Spacing.four, gap: Spacing.one },
});
