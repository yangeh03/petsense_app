import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { clockTime, duration, SOUND_NAMES } from '@/services/observation-view';
import type { SoundEvent } from '@/types/observations';

export function SoundTimeline({
  events,
  hasCoverage,
}: {
  events: SoundEvent[];
  hasCoverage: boolean;
}) {
  const theme = useTheme();
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  return (
    <View style={styles.list}>
      {!events.length && (
        <ThemedText type="small" themeColor="textSecondary">
          {hasCoverage ? '已观测时段内，暂无叫声片段。' : '收到项圈数据后，这里会记录叫声片段。'}
        </ThemedText>
      )}
      {(showAll ? events : events.slice(0, 3)).map((event) => (
        <View key={event.id}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: expanded === event.id }}
            onPress={() => setExpanded(expanded === event.id ? null : event.id)}
            style={[styles.event, { borderTopColor: theme.backgroundSelected }]}
          >
            <ThemedText type="small" themeColor="textSecondary">
              {clockTime(event.startedAt)}
            </ThemedText>
            <View style={styles.name}>
              <ThemedText type="smallBold">
                {SOUND_NAMES[event.label] ?? '声音片段'}
                {event.experimental ? ' · 实验性' : ''}
              </ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                检测到 {duration(event.observedSeconds)}
              </ThemedText>
            </View>
            <ThemedText style={{ color: theme.textSecondary }}>
              {expanded === event.id ? '−' : '+'}
            </ThemedText>
          </Pressable>
          {expanded === event.id && (
            <View style={[styles.detail, { backgroundColor: theme.background }]}>
              <ThemedText type="small" themeColor="textSecondary">
                {clockTime(event.startedAt)}—{clockTime(event.lastSeenAt)} · 模型置信度{' '}
                {Math.round(event.confidence * 100)}%
              </ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                相邻识别合并为一段，不代表单声次数。
                {event.experimental ? '呜咽识别仍处于实验阶段。' : ''}当前上报不包含可播放录音。
              </ThemedText>
            </View>
          )}
        </View>
      ))}
      {events.length > 3 && (
        <Pressable
          accessibilityRole="button"
          onPress={() => setShowAll(!showAll)}
          style={styles.more}
        >
          <ThemedText type="smallBold" style={{ color: theme.tint }}>
            {showAll ? '收起记录' : `查看最近${events.length}段记录`}
          </ThemedText>
        </Pressable>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  list: { gap: 4 },
  event: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
    paddingVertical: 14,
    borderTopWidth: 1,
  },
  name: { flex: 1, gap: 3 },
  detail: { borderRadius: 12, padding: 12, gap: 6 },
  more: { alignSelf: 'flex-start', paddingVertical: 10 },
});
