import { useEffect } from 'react';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated';
import { StyleSheet, View } from 'react-native';

import { useTheme } from '@/hooks/use-theme';
import type { TelemetryDisplayState } from '@/types/telemetry';

/**
 * 连接状态圆点：有数据流时（streaming / replaying）带呼吸光晕动画。
 *
 * 颜色一律取主题语义色（AGENTS.md：颜色不要在页面里写死）：
 * 绿色=在线/正常，橙=等待中，蓝=回放，灰=离线/未登录。
 */
export function ConnectionDot({ displayState }: { displayState: TelemetryDisplayState }) {
  const theme = useTheme();
  const glow = useSharedValue(0);
  const color = dotColor(displayState, theme);

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

type ThemeColors = ReturnType<typeof useTheme>;

function dotColor(state: TelemetryDisplayState, theme: ThemeColors): string {
  switch (state) {
    case 'streaming':
      return theme.online;
    case 'connecting':
    case 'reconnecting':
      return theme.warning;
    case 'replaying':
      return theme.info;
    case 'waiting_device':
    case 'offline':
    case 'unauthenticated':
    default:
      return theme.textSecondary;
  }
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
