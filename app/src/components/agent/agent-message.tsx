/**
 * 伙伴会话单条交换（W01 自建渲染层）：主人气泡 + 1–2 段人格回复。
 * 优先消费服务端 blocks（按发言人拆好的内容块），旧数据无 blocks 时回退
 * 到单发言人 + 标记剥离，保证历史回合展示不劣化。
 */

import { Pressable, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { ThemedText } from '@/components/themed-text';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { AgentTurn } from '@/services/agent';

type Props = {
  turn: AgentTurn;
  petId: number | null;
  petName: string | null;
  onContinueTopic: (turn: AgentTurn) => void;
  onRetry: (turn: AgentTurn) => void;
};

export function AgentMessage({ turn, petId, petName, onContinueTopic, onRetry }: Props) {
  const theme = useTheme();
  const router = useRouter();
  const segments = turn.blocks?.length
    ? turn.blocks
    : [
        {
          type: 'text' as const,
          speaker: turn.speaker,
          text: turn.reply.replace(/^【[^】]+】/, ''),
        },
      ];
  return (
    <View style={styles.exchange}>
      <View style={[styles.ownerBubble, { backgroundColor: theme.tintBackground }]}>
        <ThemedText>{turn.text}</ThemedText>
      </View>
      <View style={styles.replyColumn}>
        {segments.map((segment, index) => (
          <View key={index} style={styles.replyRow}>
            <View
              style={[
                styles.avatar,
                {
                  backgroundColor:
                    segment.speaker === 'pet' ? theme.tintBackground : theme.backgroundSelected,
                },
              ]}
            >
              {segment.speaker === 'pet' ? (
                <SymbolView
                  name={{ ios: 'pawprint.fill', android: 'pets', web: 'pets' }}
                  tintColor={theme.tint}
                  size={17}
                />
              ) : (
                <SymbolView
                  name={{ ios: 'sparkles', android: 'auto_awesome', web: 'auto_awesome' }}
                  tintColor={theme.text}
                  size={17}
                />
              )}
            </View>
            <View style={styles.replyBody}>
              <ThemedText type="smallBold" themeColor="textSecondary">
                {segment.speaker === 'pet' ? (petName ?? '爱宠') : '照料助手'}
              </ThemedText>
              <ThemedText>{segment.text || '正在接话…'}</ThemedText>
            </View>
          </View>
        ))}
        <View style={styles.replyFootnote}>
          {turn.sources.map((source, index) => (
            <Pressable
              key={index}
              accessibilityRole="button"
              style={[
                styles.source,
                {
                  backgroundColor: theme.backgroundElement,
                  borderColor: theme.backgroundSelected,
                },
              ]}
              onPress={() =>
                router.push({
                  pathname: '/records',
                  params: { petId: petId ?? undefined, start: source.startAt, end: source.endAt },
                })
              }
            >
              <SymbolView
                name={{ ios: 'chart.bar', android: 'bar_chart', web: 'bar_chart' }}
                tintColor={theme.tint}
                size={16}
              />
              <ThemedText type="smallBold" style={styles.sourceText}>
                查看这段记录
              </ThemedText>
              <SymbolView
                name={{ ios: 'chevron.right', android: 'chevron_right', web: 'chevron_right' }}
                tintColor={theme.textSecondary}
                size={13}
              />
            </Pressable>
          ))}
          {turn.status !== 'pending' ? (
            <Pressable
              accessibilityRole="button"
              style={styles.replyAction}
              onPress={() => (turn.status === 'failed' ? onRetry(turn) : onContinueTopic(turn))}
            >
              <ThemedText type="small" themeColor="textSecondary">
                {turn.status === 'failed' ? '再说一次' : '继续聊这件事'}
              </ThemedText>
            </Pressable>
          ) : null}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  exchange: { gap: 16 },
  ownerBubble: {
    alignSelf: 'flex-end',
    maxWidth: '85%',
    paddingHorizontal: 15,
    paddingVertical: 10,
    borderRadius: 20,
    borderBottomRightRadius: 6,
  },
  replyColumn: { gap: 10 },
  replyRow: { flexDirection: 'row', gap: 9, alignItems: 'flex-start' },
  replyBody: { flex: 1, gap: 6, paddingTop: 2 },
  replyFootnote: { paddingLeft: 43, gap: 4 },
  avatar: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
  },
  source: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
    marginTop: 4,
    boxShadow: Shadows.chip,
  },
  sourceText: { flex: 1 },
  replyAction: { alignSelf: 'flex-start', paddingVertical: 4 },
});
