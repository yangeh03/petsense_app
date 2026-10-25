import { Pressable, StyleSheet, View } from 'react-native';
import { useRouter } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { ReadingState } from '@/services/dashboard-readings';

const COPY: Record<ReadingState, { title: string; detail: string }> = {
  unbound: {
    title: '档案已建立 · 下一步绑定项圈',
    detail: '输入项圈编码，让健康数据和它的档案关联起来。',
  },
  waiting: {
    title: '已绑定 · 等待项圈上线',
    detail: '给项圈开机并确认已联网。收到数据后，这里会自动更新。',
  },
  offline: { title: '项圈暂未在线', detail: '请确认项圈电量和网络。下方的历史趋势仍可查看。' },
  measuring: {
    title: '项圈在线 · 等待有效采样',
    detail: '请确认传感器贴合，保持佩戴。暂未测到的体征显示为 --。',
  },
  snapshot: {
    title: '项圈在线 · 最近一次采样',
    detail: '下方为最近记录，实时连接恢复后会持续更新。',
  },
  live: { title: '正在接收实时数据', detail: '心率、血氧和体温来自已绑定的项圈。' },
};
export function DeviceStatusCard({
  state,
  updatedAt,
  batteryPercent,
}: {
  state: ReadingState;
  updatedAt: string | null;
  batteryPercent: number | null;
}) {
  const theme = useTheme();
  const router = useRouter();
  const copy = COPY[state];
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: state === 'unbound' ? theme.tintBackground : theme.backgroundElement },
      ]}
    >
      <ThemedText type="smallBold" style={{ color: state === 'live' ? theme.online : theme.text }}>
        {copy.title}
      </ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {copy.detail}
      </ThemedText>
      {state === 'unbound' ? (
        <Pressable
          onPress={() => router.push('/setup')}
          style={[styles.button, { backgroundColor: theme.tint }]}
        >
          <ThemedText type="smallBold" style={{ color: theme.backgroundElement }}>
            绑定项圈
          </ThemedText>
        </Pressable>
      ) : (
        <ThemedText type="small" themeColor="textSecondary">
          {updatedAt
            ? `最近同步 ${new Date(updatedAt).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`
            : '尚未收到设备数据'}
          {batteryPercent !== null ? ` · 电量 ${batteryPercent}%` : ''}
        </ThemedText>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  card: { padding: Spacing.three, borderRadius: 18, gap: Spacing.two },
  button: {
    alignSelf: 'flex-start',
    paddingVertical: 10,
    paddingHorizontal: Spacing.three,
    borderRadius: 12,
    marginTop: Spacing.one,
  },
});
