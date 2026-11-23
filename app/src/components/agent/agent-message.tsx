/**
 * 伙伴会话单条交换（W01 自建渲染层；W07 图片消息）：主人气泡（可含图片网格）+ 1–2 段人格回复。
 * 优先消费服务端 blocks（按发言人拆好的内容块），旧数据无 blocks 时回退
 * 到单发言人 + 标记剥离，保证历史回合展示不劣化。图片点击进灯箱。
 * W03：blocks 另含确认提案卡（type=proposal）；点击角色头像进入成员详情
 * （宠物 → 档案页，照料助手 → 助手设定页）；助手显示名从设定读取。
 * W09：blocks 另含主动关怀卡（type=care，设备事件/周期回顾/学习完成），
 * 附依据摘要与「为什么提醒我」追问入口（下钻走对话与主题依据）。
 */

import { useCallback, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import AgentImageViewer from '@/components/agent/agent-image-viewer';
import { ActionCard } from '@/components/agent/action-card';
import { ProposalCard } from '@/components/agent/proposal-card';
import { TaskProposalCard } from '@/components/agent/task-proposal-card';
import { TaskResultCard } from '@/components/agent/task-result-card';
import { WebSourceCard } from '@/components/agent/web-source-card';
import { ThemedText } from '@/components/themed-text';
import { Shadows } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import {
  agentImageSource,
  fetchAgentObservation,
  type AgentCareInfo,
  type AgentImageMeta,
  type AgentObservation,
  type AgentSource,
  type AgentTurn,
  type AgentWindowSource,
} from '@/services/agent';
import { assistantDisplayName, useCompanionSettingsStore } from '@/stores/companion-settings';

type Props = {
  turn: AgentTurn;
  petId: number | null;
  petName: string | null;
  onContinueTopic: (turn: AgentTurn) => void;
  onRetry: (turn: AgentTurn) => void;
  /** W09：主动关怀卡追问入口（预填「为什么提醒我？」并定位到该主题） */
  onAskWhy?: (turn: AgentTurn) => void;
};

type DatedSource = AgentWindowSource & { startAt: string; endAt: string };

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
function sourceSummary(source: DatedSource): string {
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

function isDatedSource(source: AgentSource): source is DatedSource {
  return (
    (source.type === 'observations' ||
      source.type === 'current_status' ||
      source.type === 'baseline') &&
    typeof source.startAt === 'string' &&
    typeof source.endAt === 'string' &&
    Number.isFinite(Date.parse(source.startAt)) &&
    Number.isFinite(Date.parse(source.endAt))
  );
}

function sourceKey(source: AgentSource) {
  if (source.type === 'web') return `web:${source.url}`;
  if (isDatedSource(source)) return `window:${source.startAt}:${source.endAt}`;
  return `unavailable:${source.type}`;
}

function minutesOf(seconds: number | null | undefined): number {
  return Math.round((seconds ?? 0) / 60);
}

/** 来源卡：点击就地展开这段窗口的关键数据；「记录页」降级为上探日/周趋势的次级入口。 */
function SourceCard({ source, petId }: { source: DatedSource; petId: number | null }) {
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

/** 单张主人图片：加载失败（网络/文件清理）显示占位与「图片不可用」，不影响文本渲染（spec §4.3） */
function OwnerImage({
  meta,
  style,
  label,
  onOpen,
}: {
  meta: AgentImageMeta;
  style: { width: number; height: number };
  label: string;
  onOpen: () => void;
}) {
  const theme = useTheme();
  const [failed, setFailed] = useState(false);
  return (
    <Pressable
      accessibilityRole="imagebutton"
      accessibilityLabel={failed ? '图片不可用' : label}
      onPress={onOpen}
      style={[styles.ownerImage, style]}
    >
      {failed ? (
        <View
          style={[
            StyleSheet.absoluteFill,
            styles.ownerImageUnavailable,
            { backgroundColor: theme.backgroundSelected },
          ]}
        >
          <SymbolView
            name={{ ios: 'photo', android: 'image', web: 'image' }}
            tintColor={theme.textSecondary}
            size={20}
          />
          <ThemedText type="small" themeColor="textSecondary">
            图片不可用
          </ThemedText>
        </View>
      ) : (
        <Image
          source={agentImageSource(meta.id)}
          style={StyleSheet.absoluteFill}
          contentFit="cover"
          transition={120}
          onError={() => setFailed(true)}
        />
      )}
    </Pressable>
  );
}

/** W09 主动关怀卡：标题 + 状态 + 依据摘要 + 「为什么提醒我」追问（证据下钻走对话） */
const CARE_STATUS_LABELS: Record<string, string> = {
  open: '提醒中',
  ongoing: '持续关注',
  recovered: '已恢复',
  closed: '已关闭',
};

function careEvidenceLines(care: AgentCareInfo): string[] {
  const lines: string[] = [];
  for (const entry of (care.evidence ?? []).slice(-2).reverse()) {
    const time = Number.isFinite(Date.parse(entry.at))
      ? new Intl.DateTimeFormat('zh-CN', {
          timeZone: SH_TZ,
          month: 'numeric',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          hour12: false,
        }).format(new Date(entry.at))
      : '';
    if (typeof entry.battery === 'number') {
      lines.push(`${time} 电量 ${Math.round(entry.battery)}%`);
    } else if (entry.healthValid === false) {
      lines.push(`${time} 读数无效`);
    } else if (entry.note) {
      lines.push(`${time} ${entry.note}`.trim());
    }
  }
  return lines;
}

function CareCard({ care, onAskWhy }: { care: AgentCareInfo; onAskWhy?: () => void }) {
  const theme = useTheme();
  const lines = careEvidenceLines(care);
  const windowLabel =
    care.windowStart && care.windowEnd
      ? ` · ${formatWindow(care.windowStart, care.windowEnd)}`
      : '';
  return (
    <View
      style={[
        styles.care,
        { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
      ]}
    >
      <View style={styles.careHead}>
        <SymbolView
          name={{ ios: 'bell.fill', android: 'notifications', web: 'notifications' }}
          tintColor={theme.tint}
          size={16}
        />
        <ThemedText type="smallBold" style={styles.careTitle}>
          {care.title || '主动提醒'}
        </ThemedText>
        {CARE_STATUS_LABELS[care.status] ? (
          <ThemedText type="small" themeColor="textSecondary">
            {CARE_STATUS_LABELS[care.status]}
            {windowLabel}
          </ThemedText>
        ) : null}
      </View>
      {lines.map((line, index) => (
        <ThemedText key={`${index}-${line}`} type="small" themeColor="textSecondary">
          依据 · {line}
        </ThemedText>
      ))}
      {onAskWhy ? (
        <Pressable accessibilityRole="button" style={styles.careWhy} onPress={onAskWhy}>
          <ThemedText type="small" style={{ color: theme.tint }}>
            为什么提醒我？
          </ThemedText>
        </Pressable>
      ) : null}
    </View>
  );
}

/** 主人气泡图片：1 张按宽高比自适应，2–3 张两列方格（W07 §4.3） */
function OwnerImages({
  images,
  onOpen,
}: {
  images: AgentImageMeta[];
  onOpen: (index: number) => void;
}) {
  if (images.length === 1) {
    const meta = images[0];
    const ratio = meta.width && meta.height ? meta.width / meta.height : 1.25;
    const width = 232;
    const height = Math.max(96, Math.min(320, Math.round(width / ratio)));
    return (
      <OwnerImage meta={meta} style={{ width, height }} label="查看图片" onOpen={() => onOpen(0)} />
    );
  }
  return (
    <View style={styles.ownerGrid}>
      {images.map((meta, index) => (
        <OwnerImage
          key={meta.id}
          meta={meta}
          style={{ width: 104, height: 104 }}
          label={`查看第 ${index + 1} 张图片`}
          onOpen={() => onOpen(index)}
        />
      ))}
    </View>
  );
}

export function AgentMessage({ turn, petId, petName, onContinueTopic, onRetry, onAskWhy }: Props) {
  const theme = useTheme();
  const router = useRouter();
  const settings = useCompanionSettingsStore((state) => state.settings);
  const assistantName = assistantDisplayName(settings);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [viewerOpen, setViewerOpen] = useState(false);
  const images = turn.images ?? [];
  const openViewer = useCallback((index: number) => {
    setViewerIndex(index);
    setViewerOpen(true);
  }, []);
  const blocks = turn.blocks?.length
    ? turn.blocks
    : [
        {
          type: 'text' as const,
          speaker: turn.speaker,
          text: turn.reply.replace(/^【[^】]+】/, ''),
        },
      ];
  const textSegments = blocks.filter((block) => block.type === 'text');
  const proposals = blocks.filter((block) => block.type === 'proposal');
  const actions = blocks.filter((block) => block.type === 'action');
  const taskResults = blocks.filter((block) => block.type === 'task_result');
  const careBlocks = blocks.filter(
    (block): block is { type: 'care'; care: AgentCareInfo } => block.type === 'care',
  );
  const hasUnknownBlocks =
    blocks.length >
    textSegments.length +
      proposals.length +
      actions.length +
      taskResults.length +
      careBlocks.length;
  // 来源去重只算一次：可展示卡（web/带日期）与未知来源回退分别派生（UI 一致性调整）。
  const uniqueSources = [...new Map((turn.sources ?? []).map((s) => [sourceKey(s), s]))];
  const cardSources = uniqueSources.filter(
    ([, source]) => source.type === 'web' || isDatedSource(source),
  );
  const unknownSources = uniqueSources.filter(
    ([, source]) => source.type !== 'web' && !isDatedSource(source),
  );

  const openMember = (speaker: 'pet' | 'assistant') =>
    router.push(speaker === 'pet' ? '/pet' : '/assistant-settings');

  return (
    <View style={styles.exchange}>
      {turn.text || images.length ? (
        <View style={[styles.ownerBubble, { backgroundColor: theme.tintBackground }]}>
          {images.length ? <OwnerImages images={images} onOpen={openViewer} /> : null}
          {turn.text ? <ThemedText>{turn.text}</ThemedText> : null}
        </View>
      ) : null}
      {images.length ? (
        <AgentImageViewer
          images={images.map((meta) => agentImageSource(meta.id))}
          imageIndex={viewerIndex}
          visible={viewerOpen}
          onRequestClose={() => setViewerOpen(false)}
          onImageIndexChange={setViewerIndex}
        />
      ) : null}
      <View style={styles.replyColumn}>
        {textSegments.map((segment, index) => (
          <View key={index} style={styles.replyRow}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={
                segment.speaker === 'pet'
                  ? `查看${petName ?? '爱宠'}的档案`
                  : `查看${assistantName}的设定`
              }
              onPress={() => openMember(segment.speaker)}
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
            </Pressable>
            <View style={styles.replyBody}>
              <Pressable accessibilityRole="button" onPress={() => openMember(segment.speaker)}>
                <ThemedText type="smallBold" themeColor="textSecondary">
                  {segment.speaker === 'pet' ? (petName ?? '爱宠') : assistantName}
                </ThemedText>
              </Pressable>
              <ThemedText>{segment.text || '正在接话…'}</ThemedText>
            </View>
          </View>
        ))}
        {hasUnknownBlocks ? (
          <ThemedText type="small" themeColor="textSecondary">
            这条消息里有暂不支持的内容。
          </ThemedText>
        ) : null}
        {proposals.map((block) =>
          block.proposal.kind === 'task_change' ? (
            <TaskProposalCard key={block.proposal.id} petId={petId} proposal={block.proposal} />
          ) : (
            <ProposalCard key={block.proposal.id} petId={petId} proposal={block.proposal} />
          ),
        )}
        {taskResults.map((block, index) => (
          <TaskResultCard key={`${block.task.deliveryId}-${index}`} block={block.task} />
        ))}
        {careBlocks.map((block, index) => (
          <CareCard
            key={`${block.care.topicId ?? 'x'}-${index}`}
            care={block.care}
            onAskWhy={() => onAskWhy?.(turn)}
          />
        ))}
        {actions.map((block) => (
          <ActionCard key={block.action.id} action={block.action} petId={petId} />
        ))}
        {cardSources.length ? (
          // 来源卡与其他结构化卡同层：与提案/任务/关怀/动作卡共用对齐的内容列，
          // 不再走缩进的脚注区，保证同一回合内卡片左右边缘一致（UI 一致性调整）。
          <View style={styles.sourceColumn}>
            {cardSources.map(([key, source]) =>
              source.type === 'web' ? (
                <WebSourceCard key={key} source={source} />
              ) : isDatedSource(source) ? (
                <SourceCard key={key} source={source} petId={petId} />
              ) : null,
            )}
          </View>
        ) : null}
        <View style={styles.replyFootnote}>
          {unknownSources.map(([key, source]) => (
            <ThemedText key={key} type="small" themeColor="textSecondary">
              {source.type === 'baseline' ? '个人基线暂无可比样本' : '暂不支持展示的来源'}
            </ThemedText>
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
    gap: 8,
  },
  ownerImage: { borderRadius: 12, overflow: 'hidden' },
  ownerImageUnavailable: { alignItems: 'center', justifyContent: 'center', gap: 6 },
  ownerGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
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
  /** 来源卡与其他结构化卡共用的对齐列：同宽、同左右边缘（不缩进到头像列） */
  sourceColumn: { gap: 6 },
  source: {
    borderWidth: 1,
    borderRadius: 12,
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
  care: { borderWidth: 1, borderRadius: 12, padding: 10, gap: 6, marginTop: 4 },
  careHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  careTitle: { flex: 1 },
  careWhy: { alignSelf: 'flex-start', paddingVertical: 2 },
  sourceLink: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingTop: 2 },
  replyAction: { alignSelf: 'flex-start', paddingVertical: 4 },
});
