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

type AgentSource = AgentTurn['sources'][number];

const SH_TZ = 'Asia/Shanghai';

function windowParts(iso: string) {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: SH_TZ,
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? '';
  return { month: get('month'), day: get('day'), hour: get('hour'), minute: get('minute') };
}

/** 来源卡窗口标签：全天/当日时段/跨天，按 Asia/Shanghai 展示（与记录页口径一致）。 */
function formatWindow(startAt: string, endAt: string): string {
  const start = windowParts(startAt);
  const end = windowParts(endAt);
  const sameDay = start.month === end.month && start.day === end.day;
  const midnightStart = start.hour === '00' && start.minute === '00';
  const midnightEnd = end.hour === '00' && end.minute === '00';
  if (!sameDay && midnightStart && midnightEnd) {
    return `${start.month}月${start.day}日 全天`;
  }
  if (sameDay) {
    return `${start.month}月${start.day}日 ${start.hour}:${start.minute}–${end.hour}:${end.minute}`;
  }
  const startTime = midnightStart ? '' : ` ${start.hour}:${start.minute}`;
  const endTime = midnightEnd ? '' : ` ${end.hour}:${end.minute}`;
  return `${start.month}月${start.day}日${startTime} – ${end.month}月${end.day}日${endTime}`;
}

/** 来源卡摘要：覆盖时长与声音事件；无覆盖如实说明（缺数据不伪装成正常）。 */
function sourceSummary(source: AgentSource): string {
  if (source.observedSeconds == null && source.soundEventCount == null) return '';
  const sounds = source.soundEventCount ?? 0;
  const minutes = Math.round((source.observedSeconds ?? 0) / 60);
  if (minutes === 0) {
    return sounds > 0 ? `无姿态记录 · 声音事件 ${sounds} 次` : '这段时间没有观测数据';
  }
  const coverage =
    minutes >= 60
      ? `${Math.floor(minutes / 60)} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ''}`
      : `${minutes} 分钟`;
  return sounds > 0 ? `覆盖 ${coverage} · 声音事件 ${sounds} 次` : `覆盖 ${coverage}`;
}

/** 相同窗口去重（模型常查重叠窗口）。 */
function dedupeSources(sources: AgentSource[]): AgentSource[] {
  return sources.filter(
    (source, index, all) =>
      all.findIndex((item) => item.startAt === source.startAt && item.endAt === source.endAt) ===
      index,
  );
}

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
          {dedupeSources(turn.sources).map((source) => {
            const label = formatWindow(source.startAt, source.endAt);
            const summary = sourceSummary(source);
            return (
              <Pressable
                key={`${source.startAt}-${source.endAt}`}
                accessibilityRole="button"
                accessibilityLabel={`${label}${summary ? `，${summary}` : ''}，查看这段记录`}
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
                <View style={styles.sourceBody}>
                  <View style={styles.sourceHead}>
                    <ThemedText type="smallBold" style={styles.sourceText}>
                      {label}
                    </ThemedText>
                    <SymbolView
                      name={{
                        ios: 'chevron.right',
                        android: 'chevron_right',
                        web: 'chevron_right',
                      }}
                      tintColor={theme.textSecondary}
                      size={13}
                    />
                  </View>
                  {summary ? (
                    <ThemedText type="small" themeColor="textSecondary">
                      {summary}
                    </ThemedText>
                  ) : null}
                </View>
              </Pressable>
            );
          })}
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
    alignItems: 'flex-start',
    gap: 9,
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
    marginTop: 4,
    boxShadow: Shadows.chip,
  },
  sourceBody: { flex: 1, gap: 2 },
  sourceHead: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  sourceText: { flex: 1 },
  replyAction: { alignSelf: 'flex-start', paddingVertical: 4 },
});
