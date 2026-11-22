/**
 * 伙伴会话单条交换（W01 自建渲染层）：主人气泡 + 1–2 段人格回复。
 * 优先消费服务端 blocks（按发言人拆好的内容块），旧数据无 blocks 时回退
 * 到单发言人 + 标记剥离，保证历史回合展示不劣化。
 */

import { useCallback, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import { ThemedText } from '@/components/themed-text';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { fetchAgentObservation, type AgentObservation, type AgentTurn } from '@/services/agent';

type Props = {
  turn: AgentTurn;
  petId: number | null;
  petName: string | null;
  onContinueTopic: (turn: AgentTurn) => void;
  onRetry: (turn: AgentTurn) => void;
};

type AgentSource = AgentTurn['sources'][number];

/** 展开详情的窗口级缓存：同一窗口多次展开不重复请求。 */
const observationCache = new Map<string, AgentObservation>();
const ACTION_LABELS: Record<string, string> = {
  walking: '走动',
  standing: '站立',
  lying: '躺卧',
  sitting: '坐',
};
const METRIC_LABELS: Record<string, string> = {
  heartRate: '平均心率',
  spo2: '平均血氧',
  bodyTemp: '平均传感器温度',
  envTemp: '平均环境温度',
};

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

function minutesOf(seconds: number | null | undefined): number {
  return Math.round((seconds ?? 0) / 60);
}

/** 来源卡：点击就地展开这段窗口的关键数据；「记录页」降级为上探日/周趋势的次级入口。 */
function SourceCard({ source, petId }: { source: AgentSource; petId: number | null }) {
  const theme = useTheme();
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<AgentObservation | null>(null);
  const [failed, setFailed] = useState(false);
  const label = formatWindow(source.startAt, source.endAt);
  const summary = sourceSummary(source);

  const toggle = useCallback(async () => {
    const next = !expanded;
    setExpanded(next);
    if (!next || !petId) return;
    const cacheKey = `${petId}:${source.startAt}:${source.endAt}`;
    const cached = observationCache.get(cacheKey);
    if (cached) {
      setDetail(cached);
      return;
    }
    setLoading(true);
    setFailed(false);
    try {
      const data = await fetchAgentObservation(petId, source.startAt, source.endAt);
      observationCache.set(cacheKey, data);
      setDetail(data);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [expanded, petId, source.startAt, source.endAt]);

  const openRecords = () =>
    router.push({
      pathname: '/records',
      params: { petId: petId ?? undefined, start: source.startAt, end: source.endAt },
    });

  const actions = detail
    ? Object.entries(detail.actionSeconds).filter(([, seconds]) => (seconds ?? 0) > 0)
    : [];
  const metrics = detail
    ? Object.entries(detail.metrics).filter(([, metric]) => metric.avg !== null)
    : [];

  return (
    <View
      style={[
        styles.source,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}${summary ? `，${summary}` : ''}，${expanded ? '收起数据' : '展开数据'}`}
        accessibilityState={{ expanded }}
        style={styles.sourceHeadButton}
        onPress={() => void toggle()}
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
              name={
                expanded
                  ? { ios: 'chevron.down', android: 'expand_more', web: 'expand_more' }
                  : { ios: 'chevron.right', android: 'chevron_right', web: 'chevron_right' }
              }
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
      {expanded ? (
        <View style={[styles.sourceDetail, { borderTopColor: theme.backgroundSelected }]}>
          {loading ? (
            <ActivityIndicator size="small" color={theme.tint} />
          ) : failed ? (
            <ThemedText type="small" themeColor="textSecondary">
              这段数据暂时没取到，可以稍后再试或去记录页查看。
            </ThemedText>
          ) : detail ? (
            <>
              {minutesOf(detail.observedSeconds) > 0 ? (
                <ThemedText type="small">
                  已观测 {minutesOf(detail.observedSeconds)} 分钟
                  {minutesOf(detail.actionObservedSeconds) > 0
                    ? `，其中姿态判定 ${minutesOf(detail.actionObservedSeconds)} 分钟`
                    : ''}
                </ThemedText>
              ) : (
                <ThemedText type="small" themeColor="textSecondary">
                  这段时间没有观测数据。
                </ThemedText>
              )}
              {actions.length ? (
                <ThemedText type="small">
                  {actions
                    .map(
                      ([key, seconds]) => `${ACTION_LABELS[key] ?? key} ${minutesOf(seconds)} 分钟`,
                    )
                    .join(' · ')}
                </ThemedText>
              ) : null}
              {detail.soundEventCount > 0 ? (
                <ThemedText type="small">声音事件 {detail.soundEventCount} 次</ThemedText>
              ) : null}
              {metrics.length ? (
                <ThemedText type="small" themeColor="textSecondary">
                  {metrics
                    .map(([key, metric]) => `${METRIC_LABELS[key] ?? key} ${metric.avg}`)
                    .join(' · ')}
                </ThemedText>
              ) : null}
            </>
          ) : null}
          <Pressable accessibilityRole="link" onPress={openRecords} style={styles.sourceLink}>
            <ThemedText type="small" style={{ color: theme.tint }}>
              在记录页看日/周趋势
            </ThemedText>
            <SymbolView
              name={{ ios: 'arrow.up.right', android: 'north_east', web: 'north_east' }}
              tintColor={theme.tint}
              size={12}
            />
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

export function AgentMessage({ turn, petId, petName, onContinueTopic, onRetry }: Props) {
  const theme = useTheme();
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
          {dedupeSources(turn.sources).map((source) => (
            <SourceCard key={`${source.startAt}-${source.endAt}`} source={source} petId={petId} />
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
    borderWidth: 1,
    borderRadius: 12,
    marginTop: 4,
    overflow: 'hidden',
    boxShadow: Shadows.chip,
  },
  sourceHeadButton: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 9,
    padding: 10,
  },
  sourceBody: { flex: 1, gap: 2 },
  sourceHead: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  sourceText: { flex: 1 },
  sourceDetail: { borderTopWidth: StyleSheet.hairlineWidth, padding: 10, paddingTop: 8, gap: 4 },
  sourceLink: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingTop: 2 },
  replyAction: { alignSelf: 'flex-start', paddingVertical: 4 },
});
