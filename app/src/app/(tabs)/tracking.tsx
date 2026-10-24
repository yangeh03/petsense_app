/*
 * 本页为手势驱动界面（拖拽平移 / 点击指挥），通过 ref 在触摸回调间传递
 * 拖拽起点与画布尺寸；这些 ref 只在触摸事件回调中读写，不会在渲染期执行。
 * react-hooks/refs 无法识别 PanResponder 回调的执行时机，因此文件级豁免。
 */
/* eslint-disable react-hooks/refs */
import { SymbolView } from 'expo-symbols';
import { useCallback, useEffect, useMemo, useRef, useState, type ComponentProps } from 'react';
import { Image, PanResponder, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { FloorPlan, type PlanTelemetry } from '@/components/floor-plan';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { MaxContentWidth, Shadows, Spacing } from '@/constants/theme';
import { useTelemetry } from '@/hooks/use-telemetry';
import { useTheme } from '@/hooks/use-theme';

type SymbolName = ComponentProps<typeof SymbolView>['name'];

type MapStyle = 'real' | 'cartoon';

const CANVAS_ASPECT = 1330 / 1182;

/**
 * 位置页（Petsense-demo 完整移植）：双模板户型图（真实/卡通）+ 演示移动引擎
 * （路线巡游 + 行为状态机 + 点击指挥）+ 拖拽平移/回正/楼层切换/实时暂停。
 * 底部摘要条的心率/体温来自真实遥测。
 */
export default function TrackingScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const { latest, status } = useTelemetry();

  const [isLive, setIsLive] = useState(true);
  const [floor, setFloor] = useState<1 | 2>(1);
  const [mapStyle, setMapStyle] = useState<MapStyle>('real');
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [recenterTick, setRecenterTick] = useState(0);
  const [centered, setCentered] = useState(false);
  const [targetPoint, setTargetPoint] = useState<{ x: number; y: number } | null>(null);
  const [targetTick, setTargetTick] = useState(0);
  const [plan, setPlan] = useState<PlanTelemetry>({ room: '客厅', behavior: '站立' });
  const [stage, setStage] = useState({ width: 0, height: 0 });

  const panRef = useRef({ x: 0, y: 0 });
  const stageRef = useRef({ width: 0, height: 0 });
  useEffect(() => {
    stageRef.current = stage;
  }, [stage]);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null>(null);

  // floor 供手势闭包读取
  const floorRef = useRef(floor);
  useEffect(() => {
    floorRef.current = floor;
  }, [floor]);

  const canvasWidth = stage.height * CANVAS_ASPECT;
  const centeringX = (stage.width - canvasWidth) / 2;

  const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

  const updatePan = useCallback((x: number, y: number) => {
    panRef.current = { x, y };
    setPan({ x, y });
  }, []);

  const recenter = useCallback(() => {
    updatePan(0, 0);
    setRecenterTick((value) => value + 1);
    setCentered(true);
    setTimeout(() => setCentered(false), 1100);
  }, [updatePan]);

  const panResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onPanResponderGrant: (event) => {
          dragRef.current = {
            startX: event.nativeEvent.pageX,
            startY: event.nativeEvent.pageY,
            originX: panRef.current.x,
            originY: panRef.current.y,
            moved: false,
          };
        },
        onPanResponderMove: (event, gesture) => {
          const drag = dragRef.current;
          if (!drag) return;
          const dx = event.nativeEvent.pageX - drag.startX;
          const dy = event.nativeEvent.pageY - drag.startY;
          if (Math.hypot(dx, dy) > 4) drag.moved = true;
          const stageWidth = stageRef.current.width;
          const canvas = stageRef.current.height * CANVAS_ASPECT;
          const maxX = Math.max(0, (canvas - stageWidth) / 2);
          updatePan(clamp(drag.originX + dx, -maxX, maxX), 0);
        },
        onPanResponderRelease: (event) => {
          const drag = dragRef.current;
          dragRef.current = null;
          if (!drag || drag.moved || floorRef.current !== 1) return;
          // 未移动的抬手 = 点击指挥：换算到 900×800 图纸坐标
          const { locationX, locationY } = event.nativeEvent;
          const canvas = stageRef.current.height * CANVAS_ASPECT;
          const centering = (stageRef.current.width - canvas) / 2;
          const x = clamp(((locationX - centering - panRef.current.x) / canvas) * 900, 42, 846);
          const y = clamp((locationY / stageRef.current.height) * 800, 48, 766);
          setTargetPoint({ x, y });
          setTargetTick((value) => value + 1);
        },
      }),
    // 手势闭包捕获稳定引用即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const battery =
    latest?.battery?.found && latest.battery.percent > 0
      ? Math.round(latest.battery.percent)
      : null;
  const health = latest?.health?.valid ? latest.health : null;
  const heartRate = health && health.heartRate > 0 ? Math.round(health.heartRate) : null;
  const bodyTemp = health && health.bodyTemp > 0 ? health.bodyTemp.toFixed(1) : null;

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentContainerStyle={styles.contentContainer}
    >
      <ThemedView style={[styles.container, { paddingTop: insets.top + Spacing.two }]}>
        {status === 'unauthenticated' && (
          <ThemedView type="backgroundElement" style={styles.loginHint}>
            <ThemedText type="small" themeColor="textSecondary">
              登录并绑定项圈后，这里会显示真实的实时定位与体征
            </ThemedText>
          </ThemedView>
        )}
        {/* 地图卡 */}
        <View
          style={[
            styles.mapCard,
            { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
          ]}
        >
          {/* 卡头：实时开关 + 电量 */}
          <View style={styles.mapHead}>
            <Pressable
              onPress={() => setIsLive((value) => !value)}
              style={[styles.livePill, { backgroundColor: isLive ? '#eef3f7' : '#fff0ed' }]}
            >
              <View style={[styles.liveDot, { backgroundColor: isLive ? '#ef5b4f' : '#d7a39e' }]} />
              <ThemedText
                type="smallBold"
                style={[styles.liveText, { color: isLive ? '#253239' : '#d94f44' }]}
              >
                {isLive ? '实时 · 连续更新' : '定位演示已暂停'}
              </ThemedText>
            </Pressable>
            <View style={styles.batteryRow}>
              <SymbolView
                name={{ ios: 'battery.75percent', android: 'battery_full', web: 'battery_full' }}
                size={16}
                tintColor={theme.textSecondary}
              />
              <ThemedText type="smallBold" themeColor="textSecondary">
                {battery !== null ? `${battery}%` : '--'}
              </ThemedText>
            </View>
          </View>

          {/* 地图舞台：可拖拽平移 + 点击指挥 */}
          <View
            style={styles.mapStage}
            onLayout={(event) => {
              const { width, height } = event.nativeEvent.layout;
              if (width !== stage.width) setStage({ width, height });
            }}
            {...panResponder.panHandlers}
          >
            {/* 画布：照片底图 + SVG 覆盖层，随平移移动 */}
            <View
              style={[
                styles.mapCanvas,
                {
                  aspectRatio: CANVAS_ASPECT,
                  transform: [{ translateX: centeringX + pan.x }, { translateY: pan.y }],
                },
              ]}
            >
              <Image
                source={
                  mapStyle === 'real'
                    ? require('@/assets/images/floorplan/floorplan-photoreal-v1.png')
                    : require('@/assets/images/floorplan/floorplan-cartoon-v1.png')
                }
                style={styles.mapImage}
                resizeMode="stretch"
              />
              <View style={StyleSheet.absoluteFill}>
                <FloorPlan
                  key={`plan-${recenterTick}`}
                  isLive={isLive}
                  floor={floor}
                  targetPoint={targetPoint}
                  targetTick={targetTick}
                  onTelemetry={setPlan}
                  petEmoji="🦮"
                  mapStyle={mapStyle}
                />
              </View>
            </View>

            {/* 模板切换：真实 / 卡通 */}
            <View style={styles.styleSwitch}>
              {(['real', 'cartoon'] as const).map((style) => (
                <Pressable key={style} onPress={() => setMapStyle(style)}>
                  <View
                    style={[
                      styles.styleSwitchItem,
                      mapStyle === style && { backgroundColor: 'rgba(255,255,255,0.9)' },
                    ]}
                  >
                    <ThemedText
                      type="smallBold"
                      style={[styles.styleSwitchText, mapStyle === style && { color: '#e9574c' }]}
                    >
                      {style === 'real' ? '真实' : '卡通'}
                    </ThemedText>
                  </View>
                </Pressable>
              ))}
            </View>

            {/* 操作提示 */}
            <View style={styles.hintPill}>
              <ThemedText type="smallBold" style={styles.hintText}>
                演示轨迹 · 拖动查看 · 点击指挥
              </ThemedText>
            </View>

            {/* 右下控制：回正 + 楼层 */}
            <View style={styles.controls}>
              <Pressable
                onPress={recenter}
                style={styles.mapControl}
                accessibilityLabel="回到起始位置"
              >
                <SymbolView
                  name={{ ios: 'scope', android: 'my_location', web: 'my_location' }}
                  size={18}
                  tintColor={theme.text}
                />
              </Pressable>
              <Pressable
                onPress={() => setFloor((value) => (value === 1 ? 2 : 1))}
                style={styles.mapControl}
                accessibilityLabel="切换楼层"
              >
                <SymbolView
                  name={{ ios: 'square.stack.3d.up', android: 'layers', web: 'layers' }}
                  size={18}
                  tintColor={theme.text}
                />
                <View style={styles.floorBadge}>
                  <ThemedText style={styles.floorBadgeText}>{floor}F</ThemedText>
                </View>
              </Pressable>
            </View>

            {/* 回正提示 */}
            {centered && (
              <View style={styles.toastPill}>
                <ThemedText style={styles.toastText}>已回到起始位置</ThemedText>
              </View>
            )}

            {/* 2F 空态 */}
            {floor === 2 && (
              <View style={styles.floorOverlay}>
                <View style={styles.floorPanel}>
                  <SymbolView
                    name={{ ios: 'house', android: 'home', web: 'home' }}
                    size={28}
                    tintColor={theme.textSecondary}
                  />
                  <ThemedText type="smallBold" style={styles.floorOverlayTitle}>
                    2F 暂无定位设备
                  </ThemedText>
                  <ThemedText
                    type="small"
                    themeColor="textSecondary"
                    style={styles.floorOverlaySub}
                  >
                    再次点击楼层按钮返回 1F
                  </ThemedText>
                </View>
              </View>
            )}
          </View>
        </View>

        {/* 状态摘要条：当前行为·房间 | 心率 | 体温 */}
        <View
          style={[
            styles.summaryRow,
            { backgroundColor: theme.backgroundElement, boxShadow: Shadows.card },
          ]}
        >
          <View style={styles.summaryStatus}>
            <ThemedText type="small" themeColor="textSecondary" style={styles.summaryLabel}>
              当前状态
            </ThemedText>
            <ThemedText style={styles.summaryValue}>
              {plan.behavior} · {isLive ? '实时' : '暂停'}
            </ThemedText>
            <ThemedText
              type="small"
              themeColor="textSecondary"
              style={styles.summarySub}
              numberOfLines={1}
            >
              位于{plan.room}
              {plan.behavior === '奔跑' ? '内移动' : '内保持静止'}
            </ThemedText>
          </View>
          <SummaryMetric
            icon={{ ios: 'heart.fill', android: 'favorite', web: 'favorite' }}
            color={theme.danger}
            value={heartRate !== null ? `${heartRate}` : '--'}
            sub="次/分 · 心率"
          />
          <SummaryMetric
            icon={{
              ios: 'thermometer.medium',
              android: 'device_thermostat',
              web: 'device_thermostat',
            }}
            color={theme.warning}
            value={bodyTemp ?? '--'}
            sub="℃ · 体温"
          />
        </View>
      </ThemedView>
    </ScrollView>
  );
}

function SummaryMetric({
  icon,
  color,
  value,
  sub,
}: {
  icon: SymbolName;
  color: string;
  value: string;
  sub: string;
}) {
  return (
    <View style={styles.summaryMetric}>
      <SymbolView name={icon} size={17} tintColor={color} />
      <ThemedText style={styles.summaryMetricValue}>{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary" style={styles.summaryMetricSub}>
        {sub}
      </ThemedText>
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
    paddingBottom: Spacing.two,
    gap: 12,
  },
  mapCard: {
    borderRadius: 28,
    overflow: 'hidden',
  },
  mapHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 8,
  },
  livePill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  liveDot: { width: 8, height: 8, borderRadius: 4 },
  liveText: { fontSize: 11, lineHeight: 15 },
  batteryRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },

  mapStage: {
    height: 460,
    margin: 10,
    marginTop: 0,
    borderRadius: 22,
    overflow: 'hidden',
    backgroundColor: '#eef0ef',
  },
  mapCanvas: {
    position: 'absolute',
    top: 0,
    left: 0,
    height: '100%',
  },
  mapImage: {
    width: '100%',
    height: '100%',
  },

  styleSwitch: {
    position: 'absolute',
    right: 12,
    top: 12,
    flexDirection: 'row',
    gap: 2,
    padding: 3,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.78)',
    backgroundColor: 'rgba(255,255,255,0.72)',
  },
  styleSwitchItem: {
    minWidth: 44,
    alignItems: 'center',
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  styleSwitchText: { fontSize: 11, lineHeight: 14, color: '#737d79' },

  hintPill: {
    position: 'absolute',
    left: 12,
    top: 12,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.8)',
    backgroundColor: 'rgba(255,255,255,0.76)',
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  hintText: { fontSize: 10, lineHeight: 13, color: '#66706d' },

  controls: {
    position: 'absolute',
    right: 12,
    bottom: 12,
    gap: 10,
  },
  mapControl: {
    width: 38,
    height: 38,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#e8e4dd',
    backgroundColor: 'rgba(255,255,255,0.95)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  floorBadge: {
    position: 'absolute',
    left: -30,
    borderRadius: 8,
    backgroundColor: '#29312f',
    paddingHorizontal: 6,
    paddingVertical: 3,
  },
  floorBadgeText: { color: '#ffffff', fontSize: 10, lineHeight: 13, fontWeight: '700' },

  toastPill: {
    position: 'absolute',
    alignSelf: 'center',
    top: '45%',
    borderRadius: 999,
    backgroundColor: 'rgba(42,50,48,0.76)',
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  toastText: { color: '#ffffff', fontSize: 11, lineHeight: 14, fontWeight: '700' },

  floorOverlay: {
    position: 'absolute',
    top: '18%',
    bottom: '19%',
    left: '2.5%',
    right: '2.5%',
    borderRadius: 30,
    backgroundColor: 'rgba(239,241,239,0.72)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  floorPanel: {
    alignItems: 'center',
    borderRadius: 24,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.82)',
    backgroundColor: 'rgba(255,255,255,0.58)',
    paddingHorizontal: 22,
    paddingVertical: 20,
  },
  floorOverlayTitle: { marginTop: 8, fontSize: 14, lineHeight: 19 },
  floorOverlaySub: { marginTop: 4, fontSize: 11, lineHeight: 15 },

  summaryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 25,
    padding: 12,
  },
  summaryStatus: {
    flex: 1.4,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: 'rgba(148,131,111,0.25)',
    paddingRight: 10,
    gap: 2,
  },
  summaryLabel: { fontSize: 10, lineHeight: 13 },
  summaryValue: { fontSize: 16, fontWeight: '700', lineHeight: 21 },
  summarySub: { fontSize: 10, lineHeight: 13 },
  summaryMetric: {
    flex: 1,
    alignItems: 'flex-start',
    paddingHorizontal: 10,
    gap: 2,
  },
  summaryMetricValue: { fontSize: 19, fontWeight: '700', lineHeight: 24 },
  summaryMetricSub: { fontSize: 9, lineHeight: 12 },
  loginHint: {
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
    alignItems: 'center',
  },
});
