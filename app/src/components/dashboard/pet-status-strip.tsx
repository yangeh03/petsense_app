import { SymbolView } from 'expo-symbols';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { soundStatus } from '@/services/sound-view';
import type { TelemetryFrame } from '@/types/telemetry';

type ChipTone = 'online' | 'tint' | 'bark' | 'muted';

/**
 * 实时状态条：项圈与声音各一枚可点胶囊，替代原先散落的按钮与状态行。
 * 音色语义——绿点=在线、品牌色=可交互、琥珀=正在吠叫、灰=暂无数据。
 */
export function PetStatusStrip({
  frame,
  current,
  bound,
  online,
  statusLabel,
  battery,
  onPressCollar,
  onPressSound,
}: {
  frame: TelemetryFrame | null;
  current: boolean;
  bound: boolean;
  online: boolean;
  statusLabel: string;
  battery: number | null;
  onPressCollar: () => void;
  onPressSound: () => void;
}) {
  const theme = useTheme();
  const sound = soundStatus(frame, current, bound);
  const barking = sound.label === '正在吠叫';
  const collarLabel = !bound
    ? '绑定项圈'
    : online
      ? `项圈在线${battery !== null ? ` · ${battery}%` : ''}`
      : statusLabel;
  const collarTone: ChipTone = !bound ? 'tint' : online ? 'online' : 'muted';
  const soundTone: ChipTone = barking ? 'bark' : sound.available ? 'tint' : 'muted';
  return (
    <View style={styles.row}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={bound ? `项圈状态：${collarLabel}` : '绑定项圈'}
        onPress={onPressCollar}
        style={[styles.chip, chipStyle(theme, collarTone)]}
      >
        <View style={[styles.dot, { backgroundColor: toneColor(theme, collarTone) }]} />
        <ThemedText type="small" style={{ color: chipText(theme, collarTone) }} numberOfLines={1}>
          {collarLabel}
        </ThemedText>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${sound.label}，查看声音诊断`}
        onPress={onPressSound}
        style={[styles.chip, chipStyle(theme, soundTone)]}
      >
        <SymbolView
          name={{ ios: 'waveform', android: 'graphic_eq', web: 'graphic_eq' }}
          size={15}
          tintColor={chipText(theme, soundTone)}
        />
        <ThemedText type="small" style={{ color: chipText(theme, soundTone) }}>
          {sound.label}
        </ThemedText>
      </Pressable>
    </View>
  );
}

function toneColor(theme: ReturnType<typeof useTheme>, tone: ChipTone) {
  if (tone === 'online') return theme.online;
  if (tone === 'muted') return theme.textSecondary;
  return theme.tint;
}

function chipStyle(theme: ReturnType<typeof useTheme>, tone: ChipTone) {
  if (tone === 'bark') return { backgroundColor: theme.warning };
  if (tone === 'muted') return { backgroundColor: theme.backgroundSelected };
  return { backgroundColor: theme.tintBackground };
}

function chipText(theme: ReturnType<typeof useTheme>, tone: ChipTone) {
  if (tone === 'bark') return theme.background;
  if (tone === 'muted') return theme.textSecondary;
  return theme.tint;
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 15,
    paddingHorizontal: 12,
    paddingVertical: 7,
    maxWidth: 230,
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
});
