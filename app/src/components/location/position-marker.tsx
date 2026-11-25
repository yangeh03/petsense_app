import { useEffect, useRef } from 'react';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedProps,
  useReducedMotion,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';
import { Circle } from 'react-native-svg';

import { motionDuration, motionTarget, type Point } from '@/services/position-motion';
import type { UwbMap } from '@/types/uwb';

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

/** Mounted per map/device. Only the marker animates; the raster stays mounted. */
export function PositionMarker({
  point,
  polygon,
  fresh,
  remainingMs,
  radius,
  fill,
  stroke,
  strokeWidth,
}: {
  point: Point;
  polygon: UwbMap['polygon'];
  fresh: boolean;
  remainingMs: number;
  radius: number;
  fill: string;
  stroke: string;
  strokeWidth: number;
}) {
  const reducedMotion = useReducedMotion();
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
  const animatedProps = useAnimatedProps(() => ({ cx: location.value.x, cy: -location.value.y }));
  return (
    <AnimatedCircle
      animatedProps={animatedProps}
      r={radius}
      fill={fill}
      stroke={stroke}
      strokeWidth={strokeWidth}
      opacity={fresh ? 1 : 0.4}
    />
  );
}
