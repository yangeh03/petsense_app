/* PanResponder refs are read/written only inside gesture callbacks. */
/* eslint-disable react-hooks/refs */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { PanResponder, Pressable, StyleSheet, View } from 'react-native';
import { Image } from 'expo-image';
import Svg, { Circle, Polyline, Text as SvgText } from 'react-native-svg';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { backgroundImageRect, clampMapPan, mapBounds } from '@/services/map-viewport';
import type { UwbMap, UwbPosition } from '@/types/uwb';

export function MapViewport({
  map,
  position,
  trail,
  fresh,
  onDragging,
}: {
  map: UwbMap;
  position: UwbPosition | null;
  trail: { x: number; y: number }[];
  fresh: boolean;
  onDragging: (value: boolean) => void;
}) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragStart = useRef({ x: 0, y: 0, pageX: 0, pageY: 0 });
  const bounds = useMemo(() => mapBounds(map), [map]);
  // Stage follows the map aspect ratio so 1.0x fills the view without letterbox bands.
  const stageHeight = useMemo(() => {
    if (!width || !bounds.width || !bounds.height) return 340;
    const fit = Math.round((width * bounds.height) / bounds.width);
    return Math.min(460, Math.max(180, fit));
  }, [width, bounds]);
  const viewport = useMemo(() => ({ width, height: stageHeight }), [width, stageHeight]);
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
  const bg = map.background;
  const point = position?.position;
  const stroke = Math.max(bounds.width, bounds.height) / 350;
  const backgroundRect = backgroundImageRect(map, bounds, viewport);
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
          {backgroundRect && (
            <Image
              source={{ uri: bg.imageDataUri }}
              style={{
                position: 'absolute',
                left: backgroundRect.left,
                top: backgroundRect.top,
                width: backgroundRect.width,
                height: backgroundRect.height,
              }}
              contentFit="fill"
              pointerEvents="none"
            />
          )}
          <Svg
            width="100%"
            height="100%"
            viewBox={`${bounds.minX} ${-(bounds.minY + bounds.height)} ${bounds.width} ${bounds.height}`}
            preserveAspectRatio="xMidYMid meet"
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
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: { width: '100%', gap: 12 },
  stage: { width: '100%', borderRadius: 18, overflow: 'hidden' },
  toolbar: { gap: 10 },
  buttons: { flexDirection: 'row', gap: 10 },
  button: { minHeight: 44, minWidth: 44, borderRadius: 12, padding: 12, alignItems: 'center' },
});
