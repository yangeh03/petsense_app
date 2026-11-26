import { useEffect, useRef, useState } from 'react';
import { Image } from 'expo-image';
import { StyleSheet } from 'react-native';
import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import type { MapPet } from '@/services/map-pet';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';

import { motionDuration, motionTarget, type Point } from '@/services/position-motion';
import type { UwbMap } from '@/types/uwb';

/** Mounted per map/device. Only the marker animates; the raster stays mounted. */
export function PositionMarker({
  point,
  polygon,
  fresh,
  remainingMs,
  bounds,
  viewport,
  zoom,
  pet,
}: {
  point: Point;
  polygon: UwbMap['polygon'];
  fresh: boolean;
  remainingMs: number;
  bounds: { minX: number; minY: number; width: number; height: number };
  viewport: { width: number; height: number };
  zoom: number;
  pet?: MapPet;
}) {
  const theme = useTheme();
  const [failedAsset, setFailedAsset] = useState<string | null>(null);
  const size = 40 / zoom;
  const reducedMotion = useReducedMotion();
  const playable =
    fresh && pet?.current && pet.animationUrl && failedAsset !== pet.animationUrl && !reducedMotion;
  const location = useSharedValue(motionTarget(null, point, polygon));
  const wasFresh = useRef(false);
  const lastTarget = useRef<Point | null>(null);
  useEffect(() => () => cancelAnimation(location), [location]);
  useEffect(() => {
    if (!fresh) {
      cancelAnimation(location);
      wasFresh.current = false;
      return;
    }
    if (wasFresh.current && lastTarget.current?.x === point.x && lastTarget.current?.y === point.y)
      return;
    cancelAnimation(location);
    const target = motionTarget(
      wasFresh.current ? location.get() : null,
      { x: point.x, y: point.y },
      polygon,
    );
    location.set(
      wasFresh.current && !reducedMotion
        ? withTiming(target, { duration: motionDuration(remainingMs), easing: Easing.linear })
        : target,
    );
    wasFresh.current = true;
    lastTarget.current = { x: point.x, y: point.y };
  }, [point.x, point.y, polygon, fresh, remainingMs, reducedMotion, location]);
  const fit = Math.min(viewport.width / bounds.width, viewport.height / bounds.height);
  const animatedStyle = useAnimatedStyle(() => ({
    transform: [
      {
        translateX:
          (viewport.width - bounds.width * fit) / 2 +
          (location.value.x - bounds.minX) * fit -
          size / 2,
      },
      {
        translateY:
          (viewport.height - bounds.height * fit) / 2 +
          (bounds.minY + bounds.height - location.value.y) * fit -
          size / 2,
      },
    ],
  }));
  return (
    <Animated.View
      pointerEvents="none"
      accessibilityLabel={`${pet?.name ?? '宠物'} · ${fresh ? '当前位置' : '最后位置'} · ${pet?.label ?? '动作待识别'}`}
      style={[
        styles.marker,
        {
          width: size,
          height: size,
          opacity: fresh ? 1 : 0.45,
          borderRadius: size / 2,
          borderWidth: 1.5 / zoom,
          borderColor: fresh ? theme.tint : theme.textSecondary,
          backgroundColor: theme.backgroundElement,
        },
        animatedStyle,
      ]}
    >
      {playable ? (
        <Image
          source={{ uri: absoluteUrl(pet.animationUrl!) }}
          autoplay
          contentFit="contain"
          transition={0}
          style={[StyleSheet.absoluteFill, { margin: 3 / zoom }]}
          onError={() => setFailedAsset(pet.animationUrl)}
        />
      ) : (
        <ThemedText style={{ fontSize: 28 / zoom, lineHeight: size }}>🐾</ThemedText>
      )}
    </Animated.View>
  );
}
const styles = StyleSheet.create({
  marker: {
    position: 'absolute',
    left: 0,
    top: 0,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
});
