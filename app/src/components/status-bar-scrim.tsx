/**
 * 状态栏遮挡层（2026-11-24 视觉走查 D-12）。
 *
 * 今日页与「我的」页没有固定顶部栏，内容滚动时直接进入状态栏区域，与系统时钟和
 * 灵动岛叠在一起（「最近更新 01:22」「暂未在线 · 电量 0%」被压住）。有顶栏的页面
 * （伙伴 / 社区）不存在这个问题。
 *
 * 这里在页面顶层盖一层与背景同色的遮挡层，只挡住状态栏那一段高度：滚动内容仍在
 * 状态栏下方滑过，但不会再和系统时间、灵动岛抢位置。
 */

import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useTheme } from '@/hooks/use-theme';

export default function StatusBarScrim() {
  const insets = useSafeAreaInsets();
  const theme = useTheme();
  if (insets.top <= 0) return null;
  return (
    <View
      pointerEvents="none"
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: insets.top,
        backgroundColor: theme.background,
        zIndex: 20,
      }}
    />
  );
}
