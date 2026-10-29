import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { MapViewport } from '@/components/location/map-viewport';
import { GpsMap } from '@/components/location/gps-map';
import { UwbSetup } from '@/components/location/uwb-setup';
import { BottomTabInset, MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useObservations } from '@/hooks/use-observations';
import { usePositionMap } from '@/hooks/use-position-map';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';
import { gpsView } from '@/services/gps-view';
import { parseTelemetryMessage } from '@/services/telemetry-service';

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
        <View style={styles.titleRow}>
          <ThemedText style={styles.title}>位置</ThemedText>
          <View style={[styles.tag, { backgroundColor: theme.tintBackground }]}>
            <ThemedText type="smallBold" style={{ color: theme.tint }}>
              室内 / GPS
            </ThemedText>
          </View>
        </View>
        <ThemedText style={styles.sectionTitle}>室内 UWB 定位</ThemedText>
        {location.map && (
          <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText type="smallBold">{location.map.name}</ThemedText>
            <MapViewport
              key={`${location.map.mapId}:${location.map.version}:${location.map.revision ?? ''}`}
              map={location.map}
              position={location.position}
              trail={location.trail}
              fresh={location.fresh}
              onDragging={setDragging}
              onRetry={location.reload}
            />
          </View>
        )}
        {location.loading && (
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
                name={{ ios: 'location.slash', android: 'location_off', web: 'location_off' }}
                size={32}
                tintColor={theme.tint}
              />
            </View>
            <ThemedText style={styles.sectionTitle}>
              {hasRanges ? '已收到测距，尚未完成户型标定' : '等待室内定位数据'}
            </ThemedText>
            <ThemedText themeColor="textSecondary" style={styles.description}>
              {hasRanges
                ? '项圈提供了真实测距数据，但尚不能据此确定房间。完成锚点与户型标定后，才能显示位置。'
                : '当前没有收到可用的 UWB 坐标。完成锚点与地图标定后，这里会显示室内位置。'}
            </ThemedText>
            <Pressable
              accessibilityRole="button"
              onPress={() => router.push('/monitor')}
              style={[styles.button, { backgroundColor: theme.tintBackground }]}
            >
              <ThemedText type="smallBold" style={{ color: theme.tint }}>
                查看项圈能力 ›
              </ThemedText>
            </Pressable>
          </View>
        )}
        <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText style={styles.sectionTitle}>GPS 定位</ThemedText>
          <ThemedText
            type="smallBold"
            style={{ color: gps.point ? theme.online : theme.textSecondary }}
          >
            {gps.status}
          </ThemedText>
          {gps.point ? (
            <>
              <GpsMap key={dashboard?.pet?.device_id} point={gps.point} onDragging={setDragging} />
              <ThemedText type="small" themeColor="textSecondary">
                纬度 {gps.point.latitude.toFixed(7)} · 经度 {gps.point.longitude.toFixed(7)}
              </ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                定位时间（UTC）{gps.utc}
              </ThemedText>
            </>
          ) : (
            <ThemedText type="small" themeColor="textSecondary">
              收到项圈的有效卫星定位后，地图会在这里显示实时位置。坐标过期时会停止显示定位点。
            </ThemedText>
          )}
          <ThemedText type="small" themeColor="textSecondary">
            位置来自项圈 GNSS，使用 WGS84 坐标；底图需要联网。
          </ThemedText>
        </View>
        {dashboard?.pet?.device_id && (
          <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <UwbSetup
              key={dashboard.pet.device_id}
              deviceId={dashboard.pet.device_id}
              onPublished={location.reload}
              onDragging={setDragging}
            />
          </View>
        )}
        {location.error !== '' && (
          <Pressable accessibilityRole="button" onPress={location.reload}>
            <ThemedText type="small" themeColor="textSecondary">
              位置暂未更新：{location.error} · 点击重试
            </ThemedText>
          </Pressable>
        )}
        <View style={[styles.info, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="smallBold">室内定位与 GPS 同时显示</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            室内位置使用重建地图和 UWB 测距，GPS 使用项圈卫星定位。两路数据分别显示，暂不自动切换。
          </ThemedText>
        </View>
      </View>
    </ScrollView>
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
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  title: { fontSize: 28, lineHeight: 36, fontWeight: '700' },
  tag: { borderRadius: 999, paddingHorizontal: Spacing.two, paddingVertical: 3 },
  card: {
    borderRadius: 22,
    padding: Spacing.five,
    gap: Spacing.three,
    boxShadow: Shadows.card,
    alignItems: 'flex-start',
  },
  icon: { padding: 18, borderRadius: 22 },
  sectionTitle: { fontSize: 21, lineHeight: 30, fontWeight: '700' },
  description: { lineHeight: 26 },
  button: { paddingHorizontal: 18, paddingVertical: 12, borderRadius: 16 },
  info: { borderTopWidth: 1, paddingTop: Spacing.four, gap: Spacing.one },
});
