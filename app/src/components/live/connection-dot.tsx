import { useEffect } from 'react';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated';
import { StyleSheet, View } from 'react-native';

import type { TelemetryDisplayState } from '@/types/telemetry';

const COLORS: Record<TelemetryDisplayState, string> = {
  streaming: '#34C77B',
  waiting_device: '#8E9AAF',
  connecting: '#E8A13A',
  reconnecting: '#E8A13A',
  offline: '#9AA0A6',
  replaying: '#5B8DEF',
  unauthenticated: '#9AA0A6',
};

/** 连接状态圆点：有数据流时（streaming / replaying）带呼吸光晕动画。 */
export function ConnectionDot({ displayState }: { displayState: TelemetryDisplayState }) {
  const glow = useSharedValue(0);
  const color = COLORS[displayState];

  useEffect(() => {
    glow.value =
      displayState === 'streaming' || displayState === 'replaying'
        ? withRepeat(withTiming(1, { duration: 900 }), -1, true)
        : withTiming(0, { duration: 200 });
  }, [displayState, glow]);

  const glowStyle = useAnimatedStyle(() => ({
    transform: [{ scale: 1 + glow.value * 1.4 }],
    opacity: glow.value * 0.5,
  }));

  return (
    <View style={styles.container}>
      {(displayState === 'streaming' || displayState === 'replaying') && (
        <Animated.View style={[styles.glow, { backgroundColor: color }, glowStyle]} />
      )}
      <View style={[styles.dot, { backgroundColor: color }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: 14,
    height: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  glow: {
    position: 'absolute',
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
});
