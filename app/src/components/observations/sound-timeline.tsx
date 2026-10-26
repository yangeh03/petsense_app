import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { ThemedText } from '@/components/themed-text';
import { useTheme } from '@/hooks/use-theme';
import { clockTime } from '@/services/observation-view';
import { groupSoundEvents } from '@/services/sound-view';
import type { SoundEvent } from '@/types/observations';

export function SoundTimeline({ events }: { events: SoundEvent[] }) {
  const theme = useTheme();
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const records = groupSoundEvents(events);
  return (
    <View>
      {!records.length && (
        <ThemedText type="small" themeColor="textSecondary">
          暂无声音记录
        </ThemedText>
      )}
      {(showAll ? records : records.slice(0, 3)).map((record) => (
        <View key={record.id}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: expanded === record.id }}
            onPress={() => setExpanded(expanded === record.id ? null : record.id)}
            style={[styles.event, { borderTopColor: theme.backgroundSelected }]}
          >
            <ThemedText type="small" themeColor="textSecondary">
              {clockTime(record.startedAt)}
            </ThemedText>
            <ThemedText type="smallBold" style={styles.name}>
              {record.label}
            </ThemedText>
            <ThemedText themeColor="textSecondary">{expanded === record.id ? '−' : '+'}</ThemedText>
          </Pressable>
          {expanded === record.id && (
            <ThemedText type="small" themeColor="textSecondary" style={styles.detail}>
              记录时间 {clockTime(record.startedAt)}—{clockTime(record.lastSeenAt)}
            </ThemedText>
          )}
        </View>
      ))}
      {records.length > 3 && (
        <Pressable
          accessibilityRole="button"
          onPress={() => setShowAll(!showAll)}
          style={styles.more}
        >
          <ThemedText type="smallBold" style={{ color: theme.tint }}>
            {showAll ? '收起' : '更多记录'}
          </ThemedText>
        </Pressable>
      )}
    </View>
  );
}
const styles = StyleSheet.create({
  event: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
    paddingVertical: 14,
    borderTopWidth: 1,
  },
  name: { flex: 1 },
  detail: { paddingBottom: 12 },
  more: { alignSelf: 'flex-start', paddingVertical: 10 },
});
