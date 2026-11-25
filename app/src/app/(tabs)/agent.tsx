/**
 * 伙伴页（W01 重写为薄 UI）：状态在 zustand store，生命周期在 use-agent-chat 与 use-agent-conversations，
 * 渲染组件在 components/agent/。键盘协调用 react-native-keyboard-controller；
 * 底部安全区沿用 NativeTabs 结论，只用一次（agent-w01-spec.md §4.2/§4.4）。
 * 多会话（W16，docs/agent-w16-spec.md §6）：一次显示一个会话，会话列表在左侧抽屉里；
 * URL 的 conversationId 是当前会话，收件箱深链按它定位到那条来源消息。
 * 双 Agent 群聊（docs/agent-dual-agent-chat-design.md §4）：逐条回复可以精确引用到某一条，
 * 失败的发言只重试那一位；账号开放时输入区多一项「两位都回」。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Keyboard,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
  type LayoutChangeEvent,
  type ScrollViewInstance,
} from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { AgentComposer } from '@/components/agent/agent-composer';
import { AgentMessage } from '@/components/agent/agent-message';
import { ConversationDrawer } from '@/components/agent/conversation-drawer';
import { ThemedText } from '@/components/themed-text';
import { Fonts, MaxContentWidth, Shadows } from '@/constants/theme';
import { useAgentChat } from '@/hooks/use-agent-chat';
import { useAgentConversations } from '@/hooks/use-agent-conversations';
import { useInboxAnchor, type InboxAnchor } from '@/hooks/use-inbox-anchor';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { useAgentChatStore } from '@/stores/agent-chat';
import { assistantDisplayName, useCompanionSettingsStore } from '@/stores/companion-settings';
import type { AgentConversation, AgentReplyMessage, AgentTurn } from '@/services/agent';
import { canRetryMessages, quotedReply, sendableTarget } from '@/services/agent-chat-turns';
import { careStatusLabel } from '@/services/care-labels';

const LATEST_MESSAGE_DISTANCE = 48;

type ReplyQuote = { name: string; text: string };

/** 发言人的显示名：与消息行首的名字一致（宠物名缺失时按通称） */
function speakerName(
  speaker: AgentTurn['speaker'],
  petName: string | null,
  assistantName: string,
): string {
  return speaker === 'pet' ? (petName ?? '爱宠') : assistantName;
}

function toId(value: string | undefined): number | null {
  const parsed = value ? Number(value) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export default function CompanionScreen() {
  const { user } = useAuth();
  return <CompanionContent key={user?.id ?? 'guest'} />;
}

/** 消息组件用到的动作，加上欢迎态的快捷建议与两个跳转 */
type MessageActions = {
  onRetry: (turn: AgentTurn) => void;
  onAskWhy: (turn: AgentTurn) => void;
  onReplyTo: (turn: AgentTurn, message: AgentReplyMessage) => void;
  suggestions: { text: string; use: () => void }[];
  openInbox: () => void;
  openLogin: () => void;
};

const SUGGESTIONS = ['它昨天走动多吗？', '打个招呼'];

/** 一个会话的消息列表。按会话 id 重新挂载，滚动位置、锚点与「回到最新」互不串台。 */
function ConversationFeed({
  turns,
  signedIn,
  petId,
  petName,
  busy,
  loading,
  hasMore,
  restoreY,
  followLatest,
  quotes,
  anchor,
  onScrollY,
  onFollowed,
  onLoadOlder,
  messageActions,
}: {
  turns: AgentTurn[];
  signedIn: boolean;
  petId: number | null;
  petName: string | null;
  busy: boolean;
  /** 回合还没取回来 */
  loading: boolean;
  hasMore: boolean;
  restoreY: number;
  /** 进来时跟到最新一条，而不是回到 restoreY */
  followLatest: boolean;
  quotes: Map<number, ReplyQuote | null>;
  anchor: InboxAnchor;
  onScrollY: (y: number) => void;
  onFollowed: () => void;
  onLoadOlder: () => void;
  messageActions: MessageActions;
}) {
  const theme = useTheme();
  const scroll = useRef<ScrollViewInstance>(null);
  const restored = useRef(false);
  const metrics = useRef({ contentHeight: 0, viewportHeight: 0, y: 0, following: false });
  const offsets = useRef<Record<number, number>>({});
  const [showLatest, setShowLatest] = useState(false);
  const [anchorY, setAnchorY] = useState<number | undefined>(undefined);

  const goToLatest = () => {
    const current = metrics.current;
    current.following = true;
    current.y = Math.max(0, current.contentHeight - current.viewportHeight);
    restored.current = true;
    scroll.current?.scrollToEnd({ animated: false });
    onScrollY(current.y);
    setShowLatest(false);
  };

  const updateLatestButton = () => {
    const { contentHeight, viewportHeight, y } = metrics.current;
    setShowLatest(
      viewportHeight > 0 && contentHeight - viewportHeight - y > LATEST_MESSAGE_DISTANCE,
    );
  };

  // 锚点定位：目标展示之后才由调用方标已读（本地命中即算展示）
  useEffect(() => {
    if (anchorY === undefined || anchor.localId === null) return;
    metrics.current.following = false;
    scroll.current?.scrollTo({ y: Math.max(0, anchorY - 96), animated: false });
  }, [anchor.localId, anchorY]);

  // 每行的位置只在它是锚点时才触发重渲染；流式输出时其余各行的布局变化不进状态
  const rememberRow = (turnId: number) => (event: LayoutChangeEvent) => {
    const y = event.nativeEvent.layout.y;
    offsets.current[turnId] = y;
    if (anchor.localId === turnId) setAnchorY(y);
  };

  return (
    <ScrollView
      ref={scroll}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="interactive"
      scrollEventThrottle={32}
      onLayout={(event) => {
        metrics.current.viewportHeight = event.nativeEvent.layout.height;
        updateLatestButton();
      }}
      onContentSizeChange={(_, height) => {
        metrics.current.contentHeight = height;
        if (followLatest && turns.length) {
          goToLatest();
          onFollowed();
        } else if (!restored.current && turns.length) {
          metrics.current.y = restoreY;
          scroll.current?.scrollTo({ y: restoreY, animated: false });
          restored.current = true;
          updateLatestButton();
        } else if (metrics.current.following) {
          goToLatest();
        } else {
          updateLatestButton();
        }
      }}
      onScrollBeginDrag={() => {
        metrics.current.following = false;
      }}
      onScroll={(event) => {
        if (!restored.current) return;
        const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
        const current = metrics.current;
        current.y = contentOffset.y;
        current.contentHeight = contentSize.height;
        current.viewportHeight = layoutMeasurement.height;
        current.following =
          current.contentHeight - current.viewportHeight - current.y <= LATEST_MESSAGE_DISTANCE;
        onScrollY(current.y);
        updateLatestButton();
      }}
      contentContainerStyle={[styles.feed, !turns.length && styles.emptyFeed]}
    >
      {petId && hasMore && turns.length ? (
        <Pressable style={styles.center} accessibilityRole="button" onPress={onLoadOlder}>
          <ThemedText type="smallBold" themeColor="textSecondary">
            更早的聊天
          </ThemedText>
        </Pressable>
      ) : null}
      {anchor.turn || anchor.topic || anchor.error ? (
        <View
          style={[styles.anchorCard, { backgroundColor: theme.backgroundElement }]}
          onLayout={anchor.markPresented}
        >
          <View style={styles.anchorHead}>
            <ThemedText type="smallBold">来自收件箱</ThemedText>
            <Pressable onPress={messageActions.openInbox} hitSlop={10} accessibilityRole="link">
              <ThemedText type="small" themeColor="textSecondary">
                返回收件箱
              </ThemedText>
            </Pressable>
          </View>
          {anchor.error ? (
            <ThemedText type="small" themeColor="textSecondary">
              {anchor.error}
            </ThemedText>
          ) : null}
          {anchor.turn ? (
            <AgentMessage
              turn={anchor.turn}
              petId={petId}
              petName={petName}
              onRetry={messageActions.onRetry}
              onAskWhy={messageActions.onAskWhy}
              onReplyTo={undefined}
            />
          ) : null}
          {anchor.topic ? (
            <View style={styles.anchorTopic}>
              <ThemedText type="smallBold">{anchor.topic.title}</ThemedText>
              {anchor.topic.detail ? (
                <ThemedText type="small" themeColor="textSecondary">
                  {anchor.topic.detail}
                </ThemedText>
              ) : null}
              {careStatusLabel(anchor.topic.status) ? (
                <ThemedText type="small" themeColor="textSecondary">
                  {careStatusLabel(anchor.topic.status)}
                </ThemedText>
              ) : null}
            </View>
          ) : null}
        </View>
      ) : null}
      {!turns.length && !busy && !loading && (!signedIn || petId) ? (
        <View style={styles.welcome}>
          <View
            style={[styles.avatar, styles.welcomeAvatar, { backgroundColor: theme.tintBackground }]}
          >
            <SymbolView
              name={{ ios: 'pawprint.fill', android: 'pets', web: 'pets' }}
              tintColor={theme.tint}
              size={36}
            />
          </View>
          <ThemedText style={styles.welcomeTitle}>
            {petId ? '今天想聊聊它的什么？' : '和它聊聊今天'}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {petId
              ? '和它打个招呼，或请照料助手看看最近的记录。'
              : '登录后，就能和爱宠及照料助手聊天。'}
          </ThemedText>
          {petId ? (
            <View style={styles.suggestions}>
              {messageActions.suggestions.map((idea) => (
                <Pressable
                  key={idea.text}
                  accessibilityRole="button"
                  style={[
                    styles.idea,
                    {
                      backgroundColor: theme.backgroundElement,
                      borderColor: theme.backgroundSelected,
                    },
                  ]}
                  onPress={idea.use}
                >
                  <ThemedText type="smallBold">{idea.text}</ThemedText>
                </Pressable>
              ))}
            </View>
          ) : (
            <Pressable
              accessibilityRole="button"
              style={[styles.pill, { backgroundColor: theme.tintBackground }]}
              onPress={messageActions.openLogin}
            >
              <ThemedText type="smallBold">登录 / 注册</ThemedText>
            </Pressable>
          )}
        </View>
      ) : null}
      {loading && !turns.length ? (
        <View style={[styles.welcome, styles.waiting]}>
          <ActivityIndicator size="small" color={theme.tint} />
          <ThemedText type="small" themeColor="textSecondary">
            正在读取聊天…
          </ThemedText>
        </View>
      ) : null}
      {turns.map((turn) => (
        <View key={turn.requestId || turn.id} onLayout={rememberRow(turn.id)}>
          <AgentMessage
            turn={turn}
            petId={petId}
            petName={petName}
            onRetry={messageActions.onRetry}
            onAskWhy={messageActions.onAskWhy}
            onReplyTo={messageActions.onReplyTo}
            quote={quotes.get(turn.id) ?? null}
          />
        </View>
      ))}
      {busy && !turns.some((turn) => turn.status === 'pending') ? (
        <View style={styles.waiting}>
          <ActivityIndicator size="small" color={theme.tint} />
          <ThemedText type="small" themeColor="textSecondary">
            伙伴正在接话…
          </ThemedText>
        </View>
      ) : null}
      {showLatest && turns.length > 0 ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="回到最新消息"
          onPress={goToLatest}
          style={({ pressed }) => [
            styles.latestButton,
            { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
            pressed && styles.latestPressed,
          ]}
        >
          <SymbolView
            name={{ ios: 'arrow.down', android: 'arrow_downward', web: 'arrow_downward' }}
            tintColor={theme.tintForeground}
            size={16}
          />
          <ThemedText type="smallBold">回到最新消息</ThemedText>
        </Pressable>
      ) : null}
    </ScrollView>
  );
}

function CompanionContent() {
  const { user } = useAuth();
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const linkParams = useLocalSearchParams<{
    conversationId?: string;
    topicId?: string;
    deliveryId?: string;
    turnId?: string;
    inboxId?: string;
  }>();
  const requestedId = toId(linkParams.conversationId);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(() => Keyboard.isVisible());

  // 账号标签在渲染时推导：换了账号而 store 还没重置的这一帧，不展示上一个账号的内容
  const storeOwner = useAgentChatStore((state) => state.ownerId);
  const storePetId = useAgentChatStore((state) => state.petId);
  const storePetName = useAgentChatStore((state) => state.petName);
  const chats = useAgentChatStore((state) => state.chats);
  const pageError = useAgentChatStore((state) => state.pageError);
  const dualAgent = useAgentChatStore((state) => state.dualAgent);
  const unreadElsewhere = useAgentChatStore((state) =>
    state.list.reduce((sum, item) => sum + (item.id === state.activeId ? 0 : item.unread), 0),
  );
  const own = user !== null && storeOwner === user.id;
  const petId = own ? storePetId : null;
  const petName = own ? storePetName : null;

  const conversations = useAgentConversations(user, petId);
  const agent = useAgentChat(user, { requestedId, paused: drawerOpen });
  // 下面这些回调只依赖稳定的动作，不依赖整个 agent / conversations 对象
  const {
    activeId,
    openConversation,
    switchConversation,
    reload,
    loadConversation,
    loadOlder,
    retryTurn,
    setDraft,
    setTarget,
    setReplyTo,
    setReplyToMessage,
  } = agent;
  const { refresh: refreshConversations, create, rename, remove } = conversations;
  const settings = useCompanionSettingsStore((state) => state.settings);
  const refreshSettings = useCompanionSettingsStore((state) => state.refresh);
  const assistantName = assistantDisplayName(settings); // W03：页头副标题随设定更新
  const [showLearning, setShowLearning] = useState(false);

  useEffect(() => {
    const show = Keyboard.addListener('keyboardWillShow', () => setKeyboardOpen(true));
    const hide = Keyboard.addListener('keyboardWillHide', () => setKeyboardOpen(false));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);

  const openDrawer = () => {
    Keyboard.dismiss(); // 抽屉盖住整个页面，先把键盘收起来
    setDrawerOpen(true);
  };

  useFocusEffect(
    useCallback(() => {
      void refreshSettings(); // W03：回到伙伴页拉齐助手设定（改名即时生效）
      void refreshConversations(); // 离开期间别的会话可能来了新消息
      return () => setDrawerOpen(false); // 页面失焦时关闭抽屉
    }, [refreshSettings, refreshConversations]),
  );

  const allTurns = useMemo(() => Object.values(chats).flatMap((chat) => chat.turns), [chats]);
  // 锚点在别的会话：只改 conversationId，保留定位参数
  const switchToAnchor = useCallback(
    (conversationId: number) => {
      openConversation(conversationId);
      router.setParams({ conversationId: String(conversationId) });
    },
    [openConversation, router],
  );
  const anchor = useInboxAnchor({
    params: linkParams,
    activeId: agent.activeId,
    switchTo: switchToAnchor,
    turns: allTurns,
  });

  // 引用预览：主人的气泡上标出这条话接着谁的哪一句；输入区上方的引用条同源。
  // 被引用的回合不在已加载范围时不显示（发送时仍会带上引用）
  const quotes = useMemo(() => {
    const byTurn = new Map<number, ReplyQuote | null>();
    for (const turn of agent.chat.turns) {
      const found = quotedReply(agent.chat.turns, turn.replyTo, turn.replyToMessage);
      byTurn.set(
        turn.id,
        found ? { name: speakerName(found.role, petName, assistantName), text: found.text } : null,
      );
    }
    return byTurn;
  }, [agent.chat.turns, petName, assistantName]);

  /** W09：主动关怀卡追问入口——在当前会话里预填问题并引用那张卡（照料助手用 read_care_topics 引证） */
  const onAskWhy = useCallback(
    (turn: AgentTurn) => {
      setReplyTo(turn.id);
      setTarget('assistant');
      setDraft('为什么提醒我？');
    },
    [setReplyTo, setTarget, setDraft],
  );
  /** 双 Agent 群聊：引用某一条回复——发送时带上这一条，由它的实际发言人接话 */
  const onReplyTo = useCallback(
    (turn: AgentTurn, message: AgentReplyMessage) => {
      setReplyTo(turn.id);
      setReplyToMessage(message.id);
    },
    [setReplyTo, setReplyToMessage],
  );
  const onCancelQuote = useCallback(() => {
    setReplyTo(undefined);
    setReplyToMessage(undefined);
  }, [setReplyTo, setReplyToMessage]);
  const onRetry = useCallback(
    (turn: AgentTurn) => {
      if (canRetryMessages(turn)) {
        // 群聊回合里有失败的发言：只重跑失败的那条，成功的保留，原话不需要回填
        void retryTurn(turn.id);
        return;
      }
      // 失败回合的原话回到它所在的会话的输入区，可直接重发
      const conversationId = turn.conversationId ?? activeId;
      if (conversationId === null) return;
      const chat = useAgentChatStore.getState();
      chat.setDraft(conversationId, turn.text);
      chat.setTarget(conversationId, sendableTarget(turn.target, chat.dualAgent));
      chat.clearAttachments(conversationId);
      if (turn.images?.length) {
        chat.addAttachments(
          conversationId,
          turn.images.map((meta) => ({
            key: `restore-${meta.id}`,
            localUri: null,
            width: meta.width ?? 0,
            height: meta.height ?? 0,
            imageId: meta.id,
            status: 'done' as const,
          })),
        );
      }
    },
    [retryTurn, activeId],
  );

  const createConversation = async () => {
    const conversation = await create();
    if (conversation) {
      setDrawerOpen(false);
      switchConversation(conversation.id);
    }
  };
  const removeConversation = async (conversationId: number) => {
    const result = await remove(conversationId);
    // 删的是当前会话：清掉 URL 上的会话参数，由落地规则切到下一个（没有就新建）
    if (result.ok && activeId === conversationId) {
      router.setParams({
        conversationId: undefined,
        turnId: undefined,
        deliveryId: undefined,
        topicId: undefined,
        inboxId: undefined,
      });
    }
    return result;
  };
  /** 打不开当前会话时的出路：重新取一次，或者回到最近的会话 */
  const recoverConversation = () => {
    if (agent.chat.error.includes('不存在')) router.setParams({ conversationId: undefined });
    else if (activeId !== null) void loadConversation(activeId, true);
  };

  // W07（spec §4.1）：有图未全完成（上传中或失败）不可发；纯图（无文字）可发
  const attachments = agent.chat.attachments;
  const uploading = attachments.some((item) => item.status === 'uploading');
  const hasFailed = attachments.some((item) => item.status === 'failed');
  const readyImages = attachments.filter((item) => item.status === 'done' && item.imageId);
  const canSend = Boolean(
    petId &&
    agent.activeId !== null &&
    !agent.chat.busy &&
    !uploading &&
    !hasFailed &&
    (agent.chat.draft.trim() || readyImages.length),
  );
  const canAttach = Boolean(
    petId && agent.activeId !== null && !agent.chat.busy && attachments.length < 3,
  );
  const draftQuote = quotedReply(agent.chat.turns, agent.chat.replyTo, agent.chat.replyToMessage);
  const title = petId && agent.conversation ? agent.conversation.title || '新对话' : '伙伴';

  return (
    <KeyboardAvoidingView
      style={{
        flex: 1,
        backgroundColor: theme.background,
        paddingTop: insets.top,
        paddingLeft: insets.left,
        paddingRight: insets.right,
      }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={[styles.header, { borderBottomColor: theme.backgroundSelected }]}>
        {petId ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={unreadElsewhere > 0 ? '打开会话列表，有新消息' : '打开会话列表'}
            onPress={openDrawer}
            style={styles.headerButton}
          >
            <SymbolView
              name={{ ios: 'sidebar.leading', android: 'menu', web: 'menu' }}
              tintColor={theme.tintForeground}
              size={22}
            />
            {unreadElsewhere > 0 ? (
              <View style={[styles.badgeDot, { backgroundColor: theme.tint }]} />
            ) : null}
          </Pressable>
        ) : (
          <View
            style={[styles.avatar, styles.headerAvatar, { backgroundColor: theme.tintBackground }]}
          >
            <SymbolView
              name={{ ios: 'pawprint.fill', android: 'pets', web: 'pets' }}
              tintColor={theme.tint}
              size={22}
            />
          </View>
        )}
        <View style={styles.heading}>
          <ThemedText style={styles.title} numberOfLines={1}>
            {title}
          </ThemedText>
          {petId ? (
            <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
              {`你 · ${petName ?? '爱宠'} · ${assistantName}`}
            </ThemedText>
          ) : null}
        </View>
        {petId ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="查看委托簿"
            onPress={() => router.push('/agent-tasks')}
            style={[styles.pill, { backgroundColor: theme.backgroundSelected }]}
          >
            <SymbolView
              name={{ ios: 'checklist', android: 'checklist', web: 'checklist' }}
              tintColor={theme.tint}
              size={18}
            />
          </Pressable>
        ) : null}
        {agent.learningDays !== null ? (
          <Pressable
            onPress={() => setShowLearning(!showLearning)}
            accessibilityRole="button"
            accessibilityState={{ expanded: showLearning }}
            style={[styles.pill, { backgroundColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold">
              {agent.learningState === 'ready'
                ? '已认识'
                : `认识中 ${Math.min(7, agent.learningDays)}/7`}
            </ThemedText>
          </Pressable>
        ) : null}
      </View>
      {showLearning && agent.learningDays !== null ? (
        <View style={[styles.learning, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText type="smallBold">
            {agent.learningState === 'ready' && agent.soundState === 'ready'
              ? '已经认识它的日常'
              : '慢慢认识它的日常'}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {agent.learningState === 'ready'
              ? '动作 已认识'
              : `动作 ${Math.min(7, agent.learningDays)}/7 天`}{' '}
            ·{' '}
            {agent.soundState === 'ready'
              ? '声音 已认识'
              : `声音 ${Math.min(7, agent.soundDays)}/7 天`}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {agent.learningState === 'ready' && agent.soundState === 'ready'
              ? '动作与声音的个人日常参照已建立。'
              : '它的个人日常参照还在建立，多记录几天就好了。'}
          </ThemedText>
        </View>
      ) : null}
      <View style={styles.conversation}>
        {agent.unloaded && agent.chat.error && petId ? (
          <Pressable
            accessibilityRole="button"
            onPress={recoverConversation}
            style={[styles.center, { flex: 1 }]}
          >
            <ThemedText type="small" themeColor="danger">
              {agent.chat.error}
            </ThemedText>
            <ThemedText type="smallBold" themeColor="tintForeground">
              {agent.chat.error.includes('不存在') ? '回到最近的会话' : '重试'}
            </ThemedText>
          </Pressable>
        ) : (
          <ConversationFeed
            key={agent.activeId ?? 'none'}
            turns={own ? agent.chat.turns : []}
            signedIn={user !== null}
            petId={petId}
            petName={petName}
            busy={agent.chat.busy}
            loading={petId !== null && agent.unloaded}
            hasMore={agent.chat.hasMore}
            restoreY={agent.chat.scrollY}
            followLatest={agent.chat.followLatest}
            quotes={quotes}
            anchor={anchor}
            onScrollY={agent.setScrollY}
            onFollowed={() => agent.activeId !== null && agent.setFollowLatest(false)}
            onLoadOlder={() => void loadOlder()}
            messageActions={{
              onRetry,
              onAskWhy,
              onReplyTo,
              openInbox: () => router.push('/agent-inbox'),
              openLogin: () => router.push('/login'),
              suggestions: SUGGESTIONS.map((idea, index) => ({
                text: idea,
                use: () => {
                  setTarget(index === 0 ? 'assistant' : 'pet');
                  setDraft(index === 0 ? idea : '你好呀！');
                },
              })),
            }}
          />
        )}
      </View>
      {pageError ? (
        <Pressable style={styles.error} accessibilityRole="button" onPress={() => void reload()}>
          <ThemedText type="small" themeColor="danger">
            {pageError}
          </ThemedText>
        </Pressable>
      ) : agent.chat.error ? (
        <Pressable
          style={styles.error}
          accessibilityRole="button"
          onPress={() => void agent.send()}
        >
          <ThemedText type="small" themeColor="danger">
            {agent.chat.error}
          </ThemedText>
        </Pressable>
      ) : null}
      {petId && agent.activeId !== null ? (
        <View style={styles.footerWrap}>
          <AgentComposer
            draft={agent.chat.draft}
            target={agent.chat.target}
            petName={petName}
            busy={agent.chat.busy}
            canSend={canSend}
            keyboardOpen={keyboardOpen && Platform.OS === 'ios'}
            bottomInset={insets.bottom}
            attachments={attachments}
            canAttach={canAttach}
            dualAgent={dualAgent}
            quote={
              draftQuote
                ? {
                    name: speakerName(draftQuote.role, petName, assistantName),
                    text: draftQuote.text,
                  }
                : null
            }
            onCancelQuote={onCancelQuote}
            onDraft={agent.setDraft}
            onTarget={agent.setTarget}
            onSend={() => void agent.send()}
            onPick={(source) => void agent.pickImages(source)}
            onRetryAttachment={agent.retryAttachment}
            onRemoveAttachment={agent.removeAttachment}
          />
        </View>
      ) : null}
      {petId ? (
        <ConversationDrawer
          visible={drawerOpen}
          list={conversations.list}
          activeId={agent.activeId}
          loading={conversations.loading}
          loaded={conversations.loaded}
          error={conversations.error}
          busy={conversations.busy}
          onClose={() => setDrawerOpen(false)}
          onSelect={(conversation: AgentConversation) => switchConversation(conversation.id)}
          onCreate={createConversation}
          onRename={rename}
          onRemove={removeConversation}
          onRetry={() => void refreshConversations()}
        />
      ) : null}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  heading: { flex: 1 },
  title: { fontFamily: Fonts.rounded, fontSize: 26, lineHeight: 34, fontWeight: '700' },
  avatar: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
  },
  headerAvatar: { width: 44, height: 44, borderRadius: 22 },
  headerButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badgeDot: {
    position: 'absolute',
    top: 10,
    right: 10,
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  welcomeAvatar: {
    width: 74,
    height: 74,
    borderRadius: 25,
    marginBottom: 6,
    boxShadow: Shadows.chip,
  },
  pill: { borderRadius: 20, paddingHorizontal: 12, paddingVertical: 8, alignSelf: 'center' },
  learning: { marginHorizontal: 18, marginTop: 8, borderRadius: 16, padding: 12, gap: 4 },
  feed: {
    flexGrow: 1,
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
    padding: 18,
    gap: 22,
  },
  emptyFeed: { justifyContent: 'center' },
  conversation: { flex: 1, minHeight: 0 },
  welcome: { alignItems: 'center', gap: 10, paddingBottom: 24 },
  welcomeTitle: {
    fontFamily: Fonts.rounded,
    fontSize: 24,
    lineHeight: 33,
    fontWeight: '700',
    textAlign: 'center',
  },
  suggestions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 8,
    marginTop: 10,
  },
  idea: { borderWidth: 1, borderRadius: 18, paddingHorizontal: 13, paddingVertical: 9 },
  center: { alignSelf: 'center', padding: 8 },
  latestButton: {
    position: 'absolute',
    alignSelf: 'center',
    borderWidth: StyleSheet.hairlineWidth,
    bottom: 8,
    zIndex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 44,
    paddingHorizontal: 14,
    borderRadius: 22,
    boxShadow: Shadows.chip,
  },
  latestPressed: { opacity: 0.8 },
  waiting: { flexDirection: 'row', gap: 9, alignItems: 'center' },
  error: { alignSelf: 'center', paddingHorizontal: 16, paddingVertical: 4 },
  footerWrap: {
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
  },
  anchorCard: {
    gap: 8,
    borderRadius: 16,
    padding: 12,
    marginBottom: 12,
  },
  anchorHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  anchorTopic: { gap: 4 },
});
