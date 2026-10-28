/* PanResponder refs are read/written only inside gesture callbacks. */
/* eslint-disable react-hooks/refs */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Image } from 'expo-image';
import { ActivityIndicator, PanResponder, Pressable, StyleSheet, View } from 'react-native';
import Svg, { Circle, Polyline, Text as SvgText } from 'react-native-svg';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { type MapImageLoad, updateMapImageLoad } from '@/services/map-image';
import { clampMapPan, mapBounds, mapImageRect } from '@/services/map-viewport';
import type { UwbMap, UwbPosition } from '@/types/uwb';

export function MapViewport({
  map,
  position,
  trail,
  fresh,
  onDragging,
  onRetry,
}: {
  map: UwbMap;
  position: UwbPosition | null;
  trail: { x: number; y: number }[];
  fresh: boolean;
  onDragging: (value: boolean) => void;
  onRetry: () => void;
}) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [imageLoad, setImageLoad] = useState<MapImageLoad | null>(null);
  const [showImageInfo, setShowImageInfo] = useState(false);
  const bg = map.background;
  const source = bg.imageDataUri;
  const currentSource = useRef(source);
  useLayoutEffect(() => {
    currentSource.current = source;
  }, [source]);
  const currentLoad = imageLoad?.source === source ? imageLoad : null;
  const imageError = currentLoad?.error ?? '';
  const imageReady = !!currentLoad?.decoded && !!currentLoad.displayed && !imageError;
  useEffect(() => {
    if (imageReady || imageError) return;
    const timeout = setTimeout(() => {
      setImageLoad((old) => updateMapImageLoad(old, source, 'error', '底图加载超时，请重新加载'));
    }, 12000);
    return () => clearTimeout(timeout);
  }, [source, imageReady, imageError]);
  const dragStart = useRef({ x: 0, y: 0, pageX: 0, pageY: 0 });
  const bounds = useMemo(() => mapBounds(map), [map]);
  // Stage follows the map aspect ratio so 1.0x fills the view without letterbox bands.
  const stageHeight = useMemo(() => {
    if (!width || !bounds.width || !bounds.height) return 340;
    const fit = Math.round((width * bounds.height) / bounds.width);
    return Math.min(460, Math.max(180, fit));
  }, [width, bounds]);
  const viewport = useMemo(() => ({ width, height: stageHeight }), [width, stageHeight]);
  const rasterRect = mapImageRect(map, viewport);
  const limited = clampMapPan(pan, viewport, bounds, zoom);
  const gestureState = useRef({ pan: limited, viewport, bounds, zoom, onDragging });
  useLayoutEffect(() => {
    gestureState.current = { pan: limited, viewport, bounds, zoom, onDragging };
  }, [limited, viewport, bounds, zoom, onDragging]);
  useEffect(() => () => onDragging(false), [onDragging]);
  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => gestureState.current.zoom > 1,
        onMoveShouldSetPanResponder: (_, gesture) =>
          gestureState.current.zoom > 1 && Math.hypot(gesture.dx, gesture.dy) > 4,
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (event) => {
          dragStart.current = {
            ...gestureState.current.pan,
            pageX: event.nativeEvent.pageX,
            pageY: event.nativeEvent.pageY,
          };
          gestureState.current.onDragging(true);
        },
        onPanResponderMove: (event) => {
          const state = gestureState.current;
          const next = clampMapPan(
            {
              x: dragStart.current.x + event.nativeEvent.pageX - dragStart.current.pageX,
              y: dragStart.current.y + event.nativeEvent.pageY - dragStart.current.pageY,
            },
            state.viewport,
            state.bounds,
            state.zoom,
          );
          state.pan = next;
          setPan(next);
        },
        onPanResponderRelease: () => gestureState.current.onDragging(false),
        onPanResponderTerminate: () => gestureState.current.onDragging(false),
      }),
    [],
  );
  const point = position?.position;
  const stroke = Math.max(bounds.width, bounds.height) / 350;
  return (
    <View style={styles.wrapper}>
      <View
        accessibilityLabel="室内定位地图，放大后可拖动查看"
        style={[styles.stage, { height: stageHeight, backgroundColor: theme.backgroundSelected }]}
        onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
        {...responder.panHandlers}
      >
        <View
          pointerEvents="none"
          style={[
            StyleSheet.absoluteFill,
            { transform: [{ translateX: limited.x }, { translateY: limited.y }, { scale: zoom }] },
          ]}
        >
          {width > 0 && (
            <Image
              key={source}
              accessibilityLabel="重建空间俯视底图"
              source={{ uri: source, width: bg.width, height: bg.height }}
              style={[styles.raster, rasterRect]}
              contentFit="fill"
              allowDownscaling={false}
              cachePolicy="none"
              transition={0}
              onLoad={({ source: decoded }) => {
                if (currentSource.current !== source) return;
                setImageLoad((old) =>
                  decoded.width === bg.width && decoded.height === bg.height
                    ? updateMapImageLoad(old, source, 'decoded')
                    : updateMapImageLoad(
                        old,
                        source,
                        'error',
                        '底图解码尺寸与原图不一致，请重新加载',
                      ),
                );
              }}
              onDisplay={() => {
                if (currentSource.current !== source) return;
                setImageLoad((old) => updateMapImageLoad(old, source, 'displayed'));
              }}
              onError={() => {
                if (currentSource.current !== source) return;
                setImageLoad((old) =>
                  updateMapImageLoad(old, source, 'error', '底图无法解码，请重新加载'),
                );
              }}
            />
          )}
          <Svg
            width="100%"
            height="100%"
            style={styles.overlay}
            viewBox={`${bounds.minX} ${-(bounds.minY + bounds.height)} ${bounds.width} ${bounds.height}`}
            preserveAspectRatio="xMidYMid meet"
            opacity={imageReady ? 1 : 0}
          >
            <Polyline
              points={[...map.polygon, map.polygon[0]].map(([x, y]) => `${x},${-y}`).join(' ')}
              fill="none"
              stroke={theme.tint}
              strokeWidth={stroke}
              opacity={0.6}
            />
            <Polyline
              points={trail.map((p) => `${p.x},${-p.y}`).join(' ')}
              fill="none"
              stroke={theme.tint}
              strokeWidth={stroke * 1.5}
              opacity={fresh ? 0.8 : 0.25}
            />
            {map.anchors.map((anchor) => (
              <SvgText
                key={anchor.id}
                x={anchor.position[0]}
                y={-anchor.position[1]}
                fontSize={stroke * 7}
                fill={theme.text}
                stroke={theme.background}
                strokeWidth={stroke * 0.1}
              >
                A{anchor.id}
              </SvgText>
            ))}
            {point && (
              <Circle
                cx={point.x}
                cy={-point.y}
                r={stroke * 4}
                fill={
                  fresh && position.status === 'ok' && position.quality.insideMap !== false
                    ? theme.tint
                    : theme.textSecondary
                }
                stroke={theme.background}
                strokeWidth={stroke}
                opacity={fresh ? 1 : 0.4}
              />
            )}
          </Svg>
        </View>
        {!imageReady && (
          <View
            pointerEvents="none"
            style={[styles.loading, { backgroundColor: theme.backgroundSelected }]}
          >
            {!imageError && <ActivityIndicator color={theme.tint} />}
            <ThemedText type="small" themeColor="textSecondary">
              {imageError || '正在加载底图…'}
            </ThemedText>
          </View>
        )}
      </View>
      <View style={styles.toolbar}>
        <ThemedText type="small" themeColor="textSecondary">
          放大后拖动查看 · {zoom.toFixed(1)}×
        </ThemedText>
        <View style={styles.buttons}>
          {[
            { text: '−', label: '缩小地图', action: () => setZoom((z) => Math.max(1, z - 0.5)) },
            { text: '＋', label: '放大地图', action: () => setZoom((z) => Math.min(4, z + 0.5)) },
            {
              text: '回正',
              label: '显示完整地图',
              action: () => {
                setZoom(1);
                setPan({ x: 0, y: 0 });
              },
            },
          ].map((button) => (
            <Pressable
              key={button.label}
              accessibilityRole="button"
              accessibilityLabel={button.label}
              onPress={button.action}
              style={[styles.button, { backgroundColor: theme.backgroundSelected }]}
            >
              <ThemedText type="smallBold">{button.text}</ThemedText>
            </Pressable>
          ))}
        </View>
      </View>
      {imageError ? (
        <Pressable accessibilityRole="button" accessibilityLabel="重新加载底图" onPress={onRetry}>
          <ThemedText type="smallBold" themeColor="tint">
            重新加载底图
          </ThemedText>
        </Pressable>
      ) : imageReady ? (
        <ThemedText type="small" themeColor="textSecondary">
          {!position?.position
            ? '已加载标定地图，等待项圈提供位置'
            : !fresh
              ? '定位已超时，灰色点为最后位置'
              : position.status === 'low_quality'
                ? '当前测距质量偏低，请参考实际环境'
                : position.quality.insideMap === false
                  ? '坐标位于标定范围之外，请核对锚点与地图'
                  : '正在显示项圈的真实位置'}
        </ThemedText>
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="查看底图信息"
        accessibilityState={{ expanded: showImageInfo }}
        onPress={() => setShowImageInfo((value) => !value)}
      >
        <ThemedText type="small" themeColor="textSecondary">
          底图信息
        </ThemedText>
      </Pressable>
      {showImageInfo && (
        <View style={styles.toolbar}>
          <ThemedText type="small" themeColor="textSecondary" selectable>
            {`原图：${bg.width} × ${bg.height} 像素\n地图：${map.mapId} / ${map.version}\nPNG 校验：通过${bg.byteLength ? ` · ${bg.byteLength} 字节` : ''}\n校验码：${bg.crc32 ?? '未提供'}\n解码与显示：${imageError ? '失败' : imageReady ? '成功' : '等待中'}`}
          </ThemedText>
          {!imageError && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="重新加载底图"
              onPress={onRetry}
            >
              <ThemedText type="smallBold" themeColor="tint">
                重新加载底图
              </ThemedText>
            </Pressable>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: { width: '100%', gap: 12 },
  stage: { width: '100%', borderRadius: 18, overflow: 'hidden' },
  raster: { position: 'absolute', zIndex: 0 },
  overlay: { position: 'absolute', top: 0, left: 0, zIndex: 1 },
  loading: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  toolbar: { gap: 10 },
  buttons: { flexDirection: 'row', gap: 10 },
  button: { minHeight: 44, minWidth: 44, borderRadius: 12, padding: 12, alignItems: 'center' },
});
