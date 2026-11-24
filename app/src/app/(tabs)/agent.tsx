/**
 * 伙伴页（W01 重写为薄 UI）：状态在 zustand store，生命周期在 use-agent-chat，
 * 渲染组件在 components/agent/。键盘协调用 react-native-keyboard-controller；
 * 底部安全区沿用 NativeTabs 结论，只用一次（agent-w01-spec.md §4.2/§4.4）。
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
  type ScrollViewInstance,
} from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { SymbolView } from 'expo-symbols';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { AgentComposer } from '@/components/agent/agent-composer';
import { AgentMessage } from '@/components/agent/agent-message';
import { ThemedText } from '@/components/themed-text';
import { Fonts, MaxContentWidth, Shadows } from '@/constants/theme';
import { useAgentChat } from '@/hooks/use-agent-chat';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { useAgentChatStore } from '@/stores/agent-chat';
import { assistantDisplayName, useCompanionSettingsStore } from '@/stores/companion-settings';
import type { AgentCareTopic, AgentTurn } from '@/services/agent';
import { fetchInboxItem, fetchInboxTurn, markInboxRead } from '@/services/agent-inbox';
import { careStatusLabel } from '@/services/care-labels';
import { anchorKey, findAnchorTurnId, parseAnchorParams } from '@/services/inbox-anchor';

const LATEST_MESSAGE_DISTANCE = 48;

export default function CompanionScreen() {
  const { user } = useAuth();
  return <CompanionContent key={user?.id ?? 'guest'} ownerId={user?.id ?? 0} />;
}

function CompanionContent({ ownerId }: { ownerId: number }) {
  const { user } = useAuth();
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const accountChat = useAgentChatStore();
  const {
    petId,
    petName,
    loadedFor,
    turns,
    topic,
    draft,
    target,
    attachments,
    busy,
    error,
    setDraft,
    setTarget,
    setTopic,
    setReplyTo,
    setScrollY,
  } =
    accountChat.ownerId === (user?.id ?? null)
      ? accountChat
      : {
          ...accountChat,
          petId: null,
          petName: null,
          loadedFor: null,
          turns: [],
          topic: undefined,
          draft: '',
          target: 'auto' as const,
          attachments: [],
          busy: false,
          error: '',
        };
  const {
    learningDays,
    soundDays,
    learningState,
    soundState,
    followReply,
    setFollowReply,
    reload,
    send,
    pickImages,
    retryAttachment,
    removeAttachment,
    loadOlder,
    markRead,
  } = useAgentChat(user);
  const settings = useCompanionSettingsStore((state) => state.settings);
  const refreshSettings = useCompanionSettingsStore((state) => state.refresh);
  const assistantName = assistantDisplayName(settings); // W03：页头副标题随设定更新
  const [showLearning, setShowLearning] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(() => Keyboard.isVisible());
  const scroll = useRef<ScrollViewInstance>(null);
  const restored = useRef(false);
  const scrollMetrics = useRef({ contentHeight: 0, viewportHeight: 0, y: 0, following: false });
  const [showLatest, setShowLatest] = useState(false);

  useFocusEffect(
    useCallback(() => {
      void refreshSettings(); // W03：回到伙伴页拉齐助手设定（改名即时生效）
    }, [refreshSettings]),
  );

  useEffect(() => {
    const show = Keyboard.addListener('keyboardWillShow', () => setKeyboardOpen(true));
    const hide = Keyboard.addListener('keyboardWillHide', () => setKeyboardOpen(false));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);

  const visible = useMemo(
    () =>
      loadedFor === (user?.id ?? -1)
        ? turns.filter((turn) => topic === undefined || (turn.topicId ?? turn.id) === topic)
        : [],
    [loadedFor, turns, topic, user?.id],
  );

  // W12 深链锚点：收件箱条目按稳定 id 解析到这里，定位到**那条来源消息**（不是占位路由）。
  // 查找范围是全部已加载回合（不受当前话题过滤影响）；不在已加载范围（早于第一页）
  // 时按稳定回合 id 向服务端精确取回；安静主题没有交付回合时取权威主题本身。
  // 目标**展示之后**才标已读；展示失败或账号变更后不应用，保持未读。
  const linkParams = useLocalSearchParams<{
    topicId?: string;
    deliveryId?: string;
    turnId?: string;
    inboxId?: string;
  }>();
  const linkTurnId = linkParams.turnId;
  const linkTopicId = linkParams.topicId;
  const linkDeliveryId = linkParams.deliveryId;
  const linkInboxId = linkParams.inboxId;
  const anchorTarget = useMemo(
    () =>
      parseAnchorParams({
        turnId: linkTurnId,
        topicId: linkTopicId,
        deliveryId: linkDeliveryId,
        inboxId: linkInboxId,
      }),
    [linkTurnId, linkTopicId, linkDeliveryId, linkInboxId],
  );
  const anchorIdentity = anchorKey(anchorTarget);
  const [anchorOffsets, setAnchorOffsets] = useState<Record<number, number>>({});
  const [anchor, setAnchor] = useState<{
    key: string;
    turn: AgentTurn | null;
    topic: AgentCareTopic | null;
    error: string;
    presented: boolean;
  }>({ key: '', turn: null, topic: null, error: '', presented: false });
  const ownerRef = useRef(ownerId);
  useEffect(() => {
    ownerRef.current = ownerId;
  }, [ownerId]);
  const localAnchorId = useMemo(() => findAnchorTurnId(turns, anchorTarget), [turns, anchorTarget]);
  const anchorFresh = anchor.key === anchorIdentity;
  const anchorTurn = anchorFresh ? anchor.turn : null;
  const anchorTopic = anchorFresh ? anchor.topic : null;
  const anchorError = anchorFresh ? anchor.error : '';
  const anchorPresented = localAnchorId !== null || (anchorFresh && anchor.presented);

  // 取回锚点来源：本地命中就定位，否则按稳定 id 向服务端精确取回（不依赖会话分页）。
  useEffect(() => {
    if (
      anchorTarget.turnId === null &&
      anchorTarget.topicId === null &&
      anchorTarget.deliveryId === null
    ) {
      return;
    }
    if (localAnchorId !== null) return; // 本地已命中：只滚动定位，不重复取回
    const requestOwner = ownerRef.current;
    let cancelled = false;
    (async () => {
      try {
        if (anchorTarget.turnId !== null) {
          const data = await fetchInboxTurn(anchorTarget.turnId);
          if (cancelled || ownerRef.current !== requestOwner) return; // 账号变更后不应用
          setAnchor({
            key: anchorIdentity,
            turn: data.turn,
            topic: null,
            error: '',
            presented: true,
          });
        } else if (anchorTarget.inboxId !== null) {
          const data = await fetchInboxItem(anchorTarget.inboxId);
          if (cancelled || ownerRef.current !== requestOwner) return;
          setAnchor({
            key: anchorIdentity,
            turn: null,
            topic: (data.item.source?.topic as AgentCareTopic | undefined) ?? null,
            error: '',
            presented: true,
          });
        }
      } catch {
        if (cancelled || ownerRef.current !== requestOwner) return;
        setAnchor({
          key: anchorIdentity,
          turn: null,
          topic: null,
          error: '这条通知的来源暂时打不开',
          presented: false,
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    anchorIdentity,
    localAnchorId,
    anchorTarget.turnId,
    anchorTarget.topicId,
    anchorTarget.deliveryId,
    anchorTarget.inboxId,
  ]);

  const localAnchorY = localAnchorId === null ? undefined : anchorOffsets[localAnchorId];
  // 本地命中时滚动到那条消息。新回合增高时无需再次定位旧锚点。
  useEffect(() => {
    if (localAnchorY == null || !anchorIdentity) return;
    scrollMetrics.current.following = false;
    setFollowReply(false);
    scroll.current?.scrollTo({ y: Math.max(0, localAnchorY - 96), animated: false });
  }, [anchorIdentity, localAnchorY, setFollowReply]);

  const updateLatestButton = () => {
    const { contentHeight, viewportHeight, y } = scrollMetrics.current;
    setShowLatest(
      viewportHeight > 0 && contentHeight - viewportHeight - y > LATEST_MESSAGE_DISTANCE,
    );
  };

  const goToLatest = () => {
    const metrics = scrollMetrics.current;
    metrics.following = true;
    metrics.y = Math.max(0, metrics.contentHeight - metrics.viewportHeight);
    restored.current = true;
    scroll.current?.scrollToEnd({ animated: false });
    if (useAgentChatStore.getState().scrollY !== metrics.y) setScrollY(metrics.y);
    setShowLatest(false);
  };

  // 目标已展示 → 标已读（展示失败不标）。
  useEffect(() => {
    if (!anchorPresented || anchorTarget.inboxId === null) return;
    void markInboxRead(anchorTarget.inboxId).catch(() => undefined);
  }, [anchorPresented, anchorTarget.inboxId]);
  // W07（spec §4.1）：有图未全完成（上传中或失败）不可发；纯图（无文字）可发；失败重试入口与草稿同源
  const uploading = attachments.some((item) => item.status === 'uploading');
  const hasFailed = attachments.some((item) => item.status === 'failed');
  const readyImages = attachments.filter((item) => item.status === 'done' && item.imageId);
  const canSend = Boolean(
    petId && !busy && !uploading && !hasFailed && (draft.trim() || readyImages.length),
  );
  const canAttach = Boolean(petId && !busy && attachments.length < 3);

  useFocusEffect(
    useCallback(() => {
      void markRead();
    }, [markRead]),
  );

  const onContinueTopic = useCallback(
    (turn: AgentTurn) => {
      setTopic(turn.topicId ?? turn.id);
      setReplyTo(turn.id);
    },
    [setTopic, setReplyTo],
  );
  /** W09：主动关怀卡追问入口——定位该主题并预填问题（照料助手用 read_care_topics 引用依据回答） */
  const onAskWhy = useCallback(
    (turn: AgentTurn) => {
      onContinueTopic(turn);
      setTarget('assistant');
      setDraft('为什么提醒我？');
    },
    [onContinueTopic, setTarget, setDraft],
  );
  const onRetry = useCallback((turn: AgentTurn) => {
    // 失败回合的图片已在服务端，按完成态放回输入区（失败不丢图，重试免重传）
    const chat = useAgentChatStore.getState();
    chat.setDraft(turn.text);
    chat.setTarget(turn.target);
    chat.clearAttachments();
    if (turn.images?.length) {
      chat.addAttachments(
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
  }, []);

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
      <View style={[styles.header, { borderColor: theme.backgroundSelected }]}>
        <View
          style={[styles.avatar, styles.headerAvatar, { backgroundColor: theme.tintBackground }]}
        >
          <SymbolView
            name={{ ios: 'pawprint.fill', android: 'pets', web: 'pets' }}
            tintColor={theme.tint}
            size={22}
          />
        </View>
        <View style={styles.heading}>
          <ThemedText style={styles.title}>伙伴</ThemedText>
          {petId ? (
            <ThemedText type="small" themeColor="textSecondary">
              你 · {petName ?? '爱宠'} · {assistantName}
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
        {loadedFor === (user?.id ?? -1) && learningDays !== null ? (
          <Pressable
            onPress={() => setShowLearning(!showLearning)}
            accessibilityRole="button"
            accessibilityState={{ expanded: showLearning }}
            style={[styles.pill, { backgroundColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold">
              {learningState === 'ready' ? '已认识' : `认识中 ${Math.min(7, learningDays)}/7`}
            </ThemedText>
          </Pressable>
        ) : null}
      </View>
      {showLearning && learningDays !== null ? (
        <View style={[styles.learning, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText type="smallBold">
            {learningState === 'ready' && soundState === 'ready'
              ? '已经认识它的日常'
              : '慢慢认识它的日常'}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {learningState === 'ready' ? '动作 已认识' : `动作 ${Math.min(7, learningDays)}/7 天`} ·{' '}
            {soundState === 'ready' ? '声音 已认识' : `声音 ${Math.min(7, soundDays)}/7 天`}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {learningState === 'ready' && soundState === 'ready'
              ? '动作与声音的个人日常参照已建立。'
              : '它的个人日常参照还在建立，多记录几天就好了。'}
          </ThemedText>
        </View>
      ) : null}
      {topic !== undefined ? (
        <Pressable
          style={[styles.topic, { backgroundColor: theme.tintBackground }]}
          accessibilityRole="button"
          accessibilityLabel="返回大家的聊天"
          onPress={() => setTopic(undefined)}
        >
          <ThemedText type="smallBold">正在说这件事</ThemedText>
          <SymbolView
            name={{ ios: 'xmark', android: 'close', web: 'close' }}
            tintColor={theme.textSecondary}
            size={16}
          />
        </Pressable>
      ) : null}
      <View style={styles.conversation}>
        <ScrollView
          ref={scroll}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="interactive"
          scrollEventThrottle={32}
          onLayout={(event) => {
            const metrics = scrollMetrics.current;
            metrics.viewportHeight = event.nativeEvent.layout.height;
            if (restored.current && metrics.following) goToLatest();
            else updateLatestButton();
          }}
          onContentSizeChange={(_, height) => {
            const metrics = scrollMetrics.current;
            metrics.contentHeight = height;
            if (!restored.current && loadedFor === ownerId && turns.length && !followReply) {
              metrics.y = useAgentChatStore.getState().scrollY;
              scroll.current?.scrollTo({ y: metrics.y, animated: false });
              restored.current = true;
              updateLatestButton();
            } else if (followReply || metrics.following) {
              goToLatest();
              if (followReply) setFollowReply(false);
            } else {
              updateLatestButton();
            }
          }}
          onScrollBeginDrag={() => {
            scrollMetrics.current.following = false;
            setFollowReply(false);
          }}
          onScroll={(event) => {
            if (!restored.current) return;
            const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
            const metrics = scrollMetrics.current;
            metrics.y = contentOffset.y;
            metrics.contentHeight = contentSize.height;
            metrics.viewportHeight = layoutMeasurement.height;
            metrics.following =
              metrics.contentHeight - metrics.viewportHeight - metrics.y <= LATEST_MESSAGE_DISTANCE;
            setScrollY(metrics.y);
            updateLatestButton();
          }}
          contentContainerStyle={[styles.feed, !visible.length && styles.emptyFeed]}
        >
          {petId && turns.length >= 40 ? (
            <Pressable
              style={styles.center}
              accessibilityRole="button"
              onPress={() => void loadOlder()}
            >
              <ThemedText type="smallBold" themeColor="textSecondary">
                更早的聊天
              </ThemedText>
            </Pressable>
          ) : null}
          {!visible.length && !busy && (!user || petId) ? (
            <View style={styles.welcome}>
              <View
                style={[
                  styles.avatar,
                  styles.welcomeAvatar,
                  { backgroundColor: theme.tintBackground },
                ]}
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
                  {['它昨天走动多吗？', '打个招呼'].map((idea, index) => (
                    <Pressable
                      key={idea}
                      accessibilityRole="button"
                      style={[
                        styles.idea,
                        {
                          backgroundColor: theme.backgroundElement,
                          borderColor: theme.backgroundSelected,
                        },
                      ]}
                      onPress={() => {
                        setTarget(index === 0 ? 'assistant' : 'pet');
                        setDraft(index === 0 ? idea : '你好呀！');
                      }}
                    >
                      <ThemedText type="smallBold">{idea}</ThemedText>
                    </Pressable>
                  ))}
                </View>
              ) : (
                <Pressable
                  accessibilityRole="button"
                  style={[styles.pill, { backgroundColor: theme.tintBackground }]}
                  onPress={() => router.push('/login')}
                >
                  <ThemedText type="smallBold">登录 / 注册</ThemedText>
                </Pressable>
              )}
            </View>
          ) : null}
          {anchorTurn || anchorTopic || anchorError ? (
            <View style={[styles.anchorCard, { backgroundColor: theme.backgroundElement }]}>
              <View style={styles.anchorHead}>
                <ThemedText type="smallBold">来自收件箱</ThemedText>
                <Pressable
                  onPress={() => router.push('/agent-inbox')}
                  hitSlop={10}
                  accessibilityLabel="返回收件箱"
                >
                  <ThemedText type="small" themeColor="textSecondary">
                    返回收件箱
                  </ThemedText>
                </Pressable>
              </View>
              {anchorError ? (
                <ThemedText type="small" themeColor="textSecondary">
                  {anchorError}
                </ThemedText>
              ) : null}
              {anchorTurn ? (
                <AgentMessage
                  turn={anchorTurn}
                  petId={petId}
                  petName={petName}
                  onContinueTopic={onContinueTopic}
                  onRetry={onRetry}
                  onAskWhy={onAskWhy}
                />
              ) : null}
              {anchorTopic ? (
                <View style={styles.anchorTopic}>
                  <ThemedText type="smallBold">{anchorTopic.title}</ThemedText>
                  {anchorTopic.detail ? (
                    <ThemedText type="small" themeColor="textSecondary">
                      {anchorTopic.detail}
                    </ThemedText>
                  ) : null}
                  {careStatusLabel(anchorTopic.status) ? (
                    <ThemedText type="small" themeColor="textSecondary">
                      {careStatusLabel(anchorTopic.status)}
                    </ThemedText>
                  ) : null}
                </View>
              ) : null}
            </View>
          ) : null}
          {visible.map((turn) => (
            <View
              key={turn.requestId || turn.id}
              onLayout={(event) => {
                const y = event.nativeEvent.layout.y;
                setAnchorOffsets((current) =>
                  current[turn.id] === y ? current : { ...current, [turn.id]: y },
                );
              }}
            >
              <AgentMessage
                turn={turn}
                petId={petId}
                petName={petName}
                onContinueTopic={onContinueTopic}
                onRetry={onRetry}
                onAskWhy={onAskWhy}
              />
            </View>
          ))}
          {busy && !visible.some((turn) => turn.status === 'pending') ? (
            <View style={styles.waiting}>
              <ActivityIndicator size="small" color={theme.tint} />
              <ThemedText type="small" themeColor="textSecondary">
                伙伴正在接话…
              </ThemedText>
            </View>
          ) : null}
        </ScrollView>
        {showLatest && visible.length > 0 ? (
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
      </View>
      {error ? (
        <Pressable style={styles.error} accessibilityRole="button" onPress={() => void reload()}>
          <ThemedText type="small" themeColor="danger">
            {error}
          </ThemedText>
        </Pressable>
      ) : null}
      {petId ? (
        <View style={styles.footerWrap}>
          <AgentComposer
            draft={draft}
            target={target}
            petName={petName}
            inTopic={topic !== undefined}
            busy={busy}
            canSend={canSend}
            keyboardOpen={keyboardOpen && Platform.OS === 'ios'}
            bottomInset={insets.bottom}
            attachments={attachments}
            canAttach={canAttach}
            onDraft={setDraft}
            onTarget={setTarget}
            onSend={() => void send()}
            onPick={(source) => void pickImages(source)}
            onRetryAttachment={retryAttachment}
            onRemoveAttachment={removeAttachment}
          />
        </View>
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
  title: { fontFamily: Fonts.rounded, fontSize: 22, lineHeight: 28, fontWeight: '700' },
  avatar: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
  },
  headerAvatar: { width: 44, height: 44, borderRadius: 22 },
  welcomeAvatar: {
    width: 74,
    height: 74,
    borderRadius: 25,
    marginBottom: 6,
    boxShadow: Shadows.chip,
  },
  pill: { borderRadius: 20, paddingHorizontal: 12, paddingVertical: 8, alignSelf: 'center' },
  learning: { marginHorizontal: 18, marginTop: 8, borderRadius: 16, padding: 12, gap: 4 },
  topic: {
    marginHorizontal: 18,
    marginTop: 8,
    borderRadius: 12,
    paddingVertical: 8,
    paddingHorizontal: 12,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
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
    bottom: 8,
    zIndex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 44,
    paddingHorizontal: 14,
    borderRadius: 22,
    borderWidth: StyleSheet.hairlineWidth,
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
