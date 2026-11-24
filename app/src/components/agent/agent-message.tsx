/**
 * 伙伴会话单条交换（W01 自建渲染层；W07 图片消息）：主人气泡（可含图片网格）+ 1–2 段人格回复。
 * 优先消费服务端 blocks（按发言人拆好的内容块），旧数据无 blocks 时回退
 * 到单发言人 + 标记剥离，保证历史回合展示不劣化。图片点击进灯箱。
 * W03：blocks 另含确认提案卡（type=proposal）；点击角色头像进入成员详情
 * （宠物 → 档案页，照料助手 → 助手设定页）；助手显示名从设定读取。
 * W09：blocks 另含主动关怀卡（type=care，设备事件/周期回顾/学习完成），
 * 附依据摘要与「为什么提醒我」追问入口（下钻走对话与主题依据）。
 * 双 Agent 群聊（docs/agent-dual-agent-chat-design.md §4）：带 messages 的回合逐条渲染，
 * 每条回复有自己的头像、状态、卡片与来源；失败的发言可只重试这一条，有正文的可被消息级引用。
 * 旧回合由服务端投影成只读消息，旧协议的回合仍按下面的内容块分段渲染。
 */

import { memo, useCallback, useState, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { Image } from 'expo-image';
import { SymbolView } from 'expo-symbols';
import { useRouter } from 'expo-router';
import AgentImageViewer from '@/components/agent/agent-image-viewer';
import { ActionCard } from '@/components/agent/action-card';
import { ChangeCard } from '@/components/agent/change-card';
import { AgentReplyText } from '@/components/agent/markdown-text';
import { visibleReplyText } from '@/components/agent/markdown';
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
  type AgentReplyMessage,
  type AgentSource,
  type AgentTurn,
  type AgentWindowSource,
} from '@/services/agent';
import type { AgentChangeEvidence } from '@/services/agent-change';
import { canRetryMessages, hasMessages, placeholderRole } from '@/services/agent-chat-turns';
import { careStatusLabel } from '@/services/care-labels';
import { assistantDisplayName, useCompanionSettingsStore } from '@/stores/companion-settings';

type Props = {
  turn: AgentTurn;
  petId: number | null;
  petName: string | null;
  onContinueTopic: (turn: AgentTurn) => void;
  onRetry: (turn: AgentTurn) => void;
  /** W09：主动关怀卡追问入口（预填「为什么提醒我？」并定位到该主题） */
  onAskWhy?: (turn: AgentTurn) => void;
  /** 双 Agent 群聊：引用这一条回复（定位到它所在的话题，发送时带上被引用的消息） */
  onReplyTo?: (turn: AgentTurn, message: AgentReplyMessage) => void;
  /** 主人这条话引用的回复（已发送的消息级/回合级引用）；没有引用或缺参数时为空 */
  quote?: { name: string; text: string } | null;
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
            <ThemedText type="small" style={{ color: theme.tintForeground }}>
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
  // 分钟级格式化会让相邻帧产出同一行文字，重复行只会制造噪声
  return lines.filter((line, index) => lines.indexOf(line) === index);
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
        {careStatusLabel(care.status) ? (
          <ThemedText type="small" themeColor="textSecondary">
            {careStatusLabel(care.status)}
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
          <ThemedText type="small" style={{ color: theme.tintForeground }}>
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

type CardBlock = NonNullable<AgentTurn['blocks']>[number];

/** 来源去重只算一次：可展示卡（web/带日期）与未知来源回退分别派生（UI 一致性调整）。 */
function uniqueSources(sources: AgentSource[]): [string, AgentSource][] {
  return [...new Map(sources.map((source) => [sourceKey(source), source] as const))];
}

function cardSources(sources: AgentSource[]): [string, AgentSource][] {
  return uniqueSources(sources).filter(
    ([, source]) => source.type === 'web' || isDatedSource(source),
  );
}

function fallbackSources(sources: AgentSource[]): [string, AgentSource][] {
  return uniqueSources(sources).filter(
    ([, source]) => source.type !== 'web' && !isDatedSource(source),
  );
}

/** 回复气泡容器：旧协议的分段回复与群聊的逐条回复共用 */
function Bubble({ children }: { children: ReactNode }) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.replyBubble,
        {
          backgroundColor: theme.backgroundElement,
          borderColor: theme.backgroundSelected,
        },
      ]}
    >
      {children}
    </View>
  );
}

/** 一条回复的行首：头像 + 名字，点击进入成员详情（宠物 → 档案页，照料助手 → 助手设定页） */
function ReplyRow({
  speaker,
  name,
  onOpen,
  children,
}: {
  speaker: AgentTurn['speaker'];
  name: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  const theme = useTheme();
  return (
    <View style={styles.replyRow}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={speaker === 'pet' ? `查看${name}的档案` : `查看${name}的设定`}
        onPress={onOpen}
        style={[
          styles.avatar,
          {
            backgroundColor: speaker === 'pet' ? theme.tintBackground : theme.backgroundSelected,
          },
        ]}
      >
        {speaker === 'pet' ? (
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
        <Pressable accessibilityRole="button" onPress={onOpen}>
          <ThemedText type="smallBold" themeColor="textSecondary">
            {name}
          </ThemedText>
        </Pressable>
        {children}
      </View>
    </View>
  );
}

/** 结构化卡与来源卡：与正文同列，左缘统一缩进到头像右侧的文本列（34 头像 + 9 间距 = 43）。 */
function ReplyCards({
  blocks,
  sources,
  petId,
  onAskWhy,
}: {
  blocks: CardBlock[];
  sources: AgentSource[];
  petId: number | null;
  onAskWhy?: () => void;
}) {
  const proposals = blocks.filter((block) => block.type === 'proposal');
  const actions = blocks.filter((block) => block.type === 'action');
  const taskResults = blocks.filter((block) => block.type === 'task_result');
  const careBlocks = blocks.filter(
    (block): block is { type: 'care'; care: AgentCareInfo } => block.type === 'care',
  );
  const changeBlocks = blocks.filter(
    (block): block is { type: 'personal_change'; change: AgentChangeEvidence } =>
      block.type === 'personal_change',
  );
  const hasUnknownBlocks =
    blocks.length >
    blocks.filter((block) => block.type === 'text').length +
      proposals.length +
      actions.length +
      taskResults.length +
      careBlocks.length +
      changeBlocks.length;
  const cards = cardSources(sources);
  if (!(
    hasUnknownBlocks ||
    proposals.length ||
    actions.length ||
    taskResults.length ||
    careBlocks.length ||
    changeBlocks.length ||
    cards.length
  )) {
    return null;
  }
  return (
    <View style={styles.replyCards}>
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
          onAskWhy={onAskWhy}
        />
      ))}
      {changeBlocks.map((block, index) => (
        <ChangeCard key={`${block.change.evidenceId}-${index}`} change={block.change} />
      ))}
      {actions.map((block) => (
        <ActionCard key={block.action.id} action={block.action} petId={petId} />
      ))}
      {cards.map(([key, source]) =>
        source.type === 'web' ? (
          <WebSourceCard key={key} source={source} />
        ) : isDatedSource(source) ? (
          <SourceCard key={key} source={source} petId={petId} />
        ) : null,
      )}
    </View>
  );
}

/** 没法在这里展开的来源：留在脚注区如实说明 */
function SourceFallbacks({ sources }: { sources: AgentSource[] }) {
  return (
    <>
      {fallbackSources(sources).map(([key, source]) => (
        <ThemedText key={key} type="small" themeColor="textSecondary">
          {source.type === 'baseline' ? '它的个人参照范围还在建立中' : '这条依据暂时没法在这里展开'}
        </ThemedText>
      ))}
    </>
  );
}

/** 群聊里一条回复的正文：排队、生成中（含联网核实）、失败各有如实的状态文案 */
function MessageBubble({ message }: { message: AgentReplyMessage }) {
  const theme = useTheme();
  if (message.status === 'failed' && !message.readOnly) {
    // 已流出的半截话由服务端撤回（正文为空、版本更高），这里只说明没接上
    return (
      <Bubble>
        <ThemedText type="small" themeColor="textSecondary">
          {message.errorNote ?? '这条暂时没能接上，稍后再试'}
        </ThemedText>
      </Bubble>
    );
  }
  if (message.status === 'queued') {
    return (
      <Bubble>
        <ThemedText type="small" themeColor="textSecondary">
          准备接话…
        </ThemedText>
      </Bubble>
    );
  }
  if (message.status === 'running' && message.activity === 'searching') {
    return (
      <Bubble>
        <View style={styles.searching}>
          <ActivityIndicator size="small" color={theme.tint} />
          <ThemedText type="small" themeColor="textSecondary">
            正在联网核实…
          </ThemedText>
        </View>
      </Bubble>
    );
  }
  return (
    <Bubble>
      <AgentReplyText
        text={visibleReplyText(message.text, message.status === 'running')}
        pending={message.status === 'running'}
      />
    </Bubble>
  );
}

/** 群聊的一条回复：正文、属于它的卡片与来源，以及这一条的「再试一次」「回复」 */
function GroupMessage({
  message,
  name,
  turn,
  petId,
  onOpen,
  onAskWhy,
  onRetry,
  onReplyTo,
}: {
  message: AgentReplyMessage;
  name: string;
  turn: AgentTurn;
  petId: number | null;
  onOpen: () => void;
  onAskWhy?: () => void;
  onRetry: (turn: AgentTurn) => void;
  onReplyTo?: (turn: AgentTurn, message: AgentReplyMessage) => void;
}) {
  const sources = message.sources ?? [];
  // 只重跑失败的这一条（旧回合的投影不可重试，走回合级的「再说一次」）；同轮还有人在生成时
  // 服务端不接受重试，等这一轮收尾再出现入口。有正文的才可被引用（旧回合的投影也可引用）
  const retryable = message.status === 'failed' && !message.readOnly && turn.status !== 'pending';
  const replyable = message.status === 'complete' && message.text.trim().length > 0 && !!onReplyTo;
  const fallbacks = fallbackSources(sources);
  return (
    <View style={styles.message}>
      <ReplyRow speaker={message.role} name={name} onOpen={onOpen}>
        <MessageBubble message={message} />
      </ReplyRow>
      <ReplyCards
        blocks={(message.blocks ?? []).filter((block) => block.type !== 'text')}
        sources={sources}
        petId={petId}
        onAskWhy={onAskWhy}
      />
      {retryable || replyable || fallbacks.length ? (
        <View style={styles.replyFootnote}>
          <SourceFallbacks sources={sources} />
          {retryable ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`重新发送${name}的这条回复`}
              style={styles.replyAction}
              onPress={() => onRetry(turn)}
            >
              <ThemedText type="smallBold">再试一次</ThemedText>
            </Pressable>
          ) : null}
          {replyable ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`引用${name}的这条回复`}
              style={styles.replyAction}
              onPress={() => onReplyTo?.(turn, message)}
            >
              <ThemedText type="smallBold">回复</ThemedText>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

export const AgentMessage = memo(function AgentMessage({
  turn,
  petId,
  petName,
  onContinueTopic,
  onRetry,
  onAskWhy,
  onReplyTo,
  quote,
}: Props) {
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
  const openMember = (speaker: 'pet' | 'assistant') =>
    router.push(speaker === 'pet' ? '/pet' : '/assistant-settings');
  const nameOf = (speaker: 'pet' | 'assistant') =>
    speaker === 'pet' ? (petName ?? '爱宠') : assistantName;
  const pending = turn.status === 'pending';
  const turnSources = turn.sources ?? [];
  // 群聊协议的回合带逐条回复（旧回合由服务端投影成只读消息）；旧协议的回合按内容块分段渲染
  const messages = hasMessages(turn) ? turn.messages : null;
  const blocks = turn.blocks?.length
    ? turn.blocks
    : [
        {
          type: 'text' as const,
          speaker: turn.speaker,
          text: turn.reply,
        },
      ];
  const textSegments = blocks.filter((block) => block.type === 'text');
  const unownedSources = messages
    ? turnSources.filter(
        (source) =>
          !messages.some((message) =>
            (message.sources ?? []).some((owned) => sourceKey(owned) === sourceKey(source)),
          ),
      )
    : turnSources;
  // 还没路由出谁来说话：只点名了一位（且没有引用别的发言人）时，谁接话已确定；「两位都回」
  // 与带引用时先发言的要等路由结果，不预设发言人，免得头像跳变
  const waitingRole =
    messages?.length === 0 && pending ? placeholderRole(turn.target, Boolean(turn.replyTo)) : null;

  const turnAction = !pending ? (
    <Pressable
      accessibilityRole="button"
      style={styles.replyAction}
      onPress={() => (turn.status === 'failed' ? onRetry(turn) : onContinueTopic(turn))}
    >
      <ThemedText type="smallBold">
        {turn.status === 'failed' ? '再说一次' : '继续聊这件事'}
      </ThemedText>
    </Pressable>
  ) : null;

  return (
    <View style={styles.exchange}>
      {turn.text || images.length ? (
        <View style={[styles.ownerBubble, { backgroundColor: theme.tintBackground }]}>
          {images.length ? <OwnerImages images={images} onOpen={openViewer} /> : null}
          {quote ? (
            // 这条话引用的是哪一条回复（消息级引用）；一行带过，点开原消息所在话题看全文
            <ThemedText type="small" numberOfLines={2}>
              {`引用 ${quote.name}：${quote.text}`}
            </ThemedText>
          ) : null}
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
        {messages ? (
          <>
            {waitingRole ? (
              <ReplyRow
                speaker={waitingRole}
                name={nameOf(waitingRole)}
                onOpen={() => openMember(waitingRole)}
              >
                <Bubble>
                  <AgentReplyText text="" pending />
                </Bubble>
              </ReplyRow>
            ) : messages.length === 0 && pending ? (
              <View style={styles.waiting}>
                <ActivityIndicator size="small" color={theme.tint} />
                <ThemedText type="small" themeColor="textSecondary">
                  伙伴正在接话…
                </ThemedText>
              </View>
            ) : null}
            {messages.map((message) => (
              <GroupMessage
                key={message.id}
                message={message}
                name={nameOf(message.role)}
                turn={turn}
                petId={petId}
                onOpen={() => openMember(message.role)}
                onAskWhy={() => onAskWhy?.(turn)}
                onRetry={onRetry}
                onReplyTo={onReplyTo}
              />
            ))}
            {/* 来源跟在各自的消息下面；回合级的来源只兜底显示没归属到任何一条的 */}
            {unownedSources.length || !canRetryMessages(turn) ? (
              <View style={styles.replyFootnote}>
                <SourceFallbacks sources={unownedSources} />
                {/* 失败的发言各自有「再试一次」，回合级不再提供整条重发 */}
                {canRetryMessages(turn) ? null : turnAction}
              </View>
            ) : null}
          </>
        ) : (
          <>
            {textSegments.map((segment, index) => (
              <ReplyRow
                key={index}
                speaker={segment.speaker}
                name={nameOf(segment.speaker)}
                onOpen={() => openMember(segment.speaker)}
              >
                <Bubble>
                  <AgentReplyText
                    text={visibleReplyText(segment.text, pending)}
                    pending={pending}
                  />
                </Bubble>
              </ReplyRow>
            ))}
            <ReplyCards
              blocks={blocks}
              sources={turnSources}
              petId={petId}
              onAskWhy={() => onAskWhy?.(turn)}
            />
            <View style={styles.replyFootnote}>
              <SourceFallbacks sources={turnSources} />
              {turnAction}
            </View>
          </>
        )}
      </View>
    </View>
  );
});

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
  /** 群聊的一条回复：正文、卡片与这一条的操作成组 */
  message: { gap: 10 },
  replyRow: { flexDirection: 'row', gap: 9, alignItems: 'flex-start' },
  replyBody: { flex: 1, gap: 6, paddingTop: 2 },
  replyBubble: {
    alignSelf: 'flex-start',
    maxWidth: '100%',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 20,
    borderBottomLeftRadius: 6,
    borderWidth: StyleSheet.hairlineWidth,
  },
  replyFootnote: { paddingLeft: 43, gap: 4 },
  avatar: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
  },
  /** 结构化卡与正文共用的对齐列：左缘缩进到文本列（与 replyFootnote 同 43），卡片间距沿用消息列的 10 */
  replyCards: { paddingLeft: 43, gap: 10 },
  /** 未指定发言人时的等待行：与头像同宽的转圈，后接说明 */
  waiting: { flexDirection: 'row', gap: 9, alignItems: 'center', minHeight: 34 },
  searching: { flexDirection: 'row', gap: 8, alignItems: 'center' },
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
