/**
 * 伙伴页（W01 重写为薄 UI）：状态在 zustand store，生命周期在 use-agent-chat，
 * 渲染组件在 components/agent/。键盘协调用 react-native-keyboard-controller；
 * 底部安全区沿用 NativeTabs 结论，只用一次（agent-w01-spec.md §4.2/§4.4）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
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
import { useFocusEffect, useRouter } from 'expo-router';
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
import type { AgentTurn } from '@/services/agent';

export default function CompanionScreen() {
  const { user } = useAuth();
  return <CompanionContent key={user?.id ?? 'guest'} ownerId={user?.id ?? 0} />;
}

function CompanionContent({ ownerId }: { ownerId: number }) {
  const { user } = useAuth();
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
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
  } = useAgentChatStore();
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

  const visible =
    loadedFor === (user?.id ?? -1)
      ? turns.filter((turn) => topic === undefined || (turn.topicId ?? turn.id) === topic)
      : [];
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

  const onContinueTopic = (turn: AgentTurn) => {
    setTopic(turn.topicId ?? turn.id);
    setReplyTo(turn.id);
  };
  /** W09：主动关怀卡追问入口——定位该主题并预填问题（照料助手用 read_care_topics 引用依据回答） */
  const onAskWhy = (turn: AgentTurn) => {
    setTopic(turn.topicId ?? turn.id);
    setReplyTo(turn.id);
    setTarget('assistant');
    setDraft('为什么提醒我？');
  };
  const onRetry = (turn: AgentTurn) => {
    setDraft(turn.text);
    setTarget(turn.target);
    // 失败回合的图片已在服务端，按完成态放回输入区（失败不丢图，重试免重传）
    if (turn.images?.length) {
      useAgentChatStore.getState().clearAttachments();
      useAgentChatStore.getState().addAttachments(
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
  };

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
              : '区域观察等待接入，个人日常参照仍在建立。'}
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
      <ScrollView
        ref={scroll}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="interactive"
        scrollEventThrottle={100}
        onContentSizeChange={() => {
          if (!restored.current && loadedFor === ownerId && turns.length && !followReply) {
            scroll.current?.scrollTo({ y: useAgentChatStore.getState().scrollY, animated: false });
            restored.current = true;
          } else if (followReply) {
            scroll.current?.scrollToEnd({ animated: false });
            restored.current = true;
            if (!busy) setFollowReply(false);
          }
        }}
        onScrollBeginDrag={() => setFollowReply(false)}
        onScroll={(event) => {
          if (restored.current) setScrollY(event.nativeEvent.contentOffset.y);
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
        {visible.map((turn) => (
          <AgentMessage
            key={turn.id}
            turn={turn}
            petId={petId}
            petName={petName}
            onContinueTopic={onContinueTopic}
            onRetry={onRetry}
            onAskWhy={onAskWhy}
          />
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
  waiting: { flexDirection: 'row', gap: 9, alignItems: 'center' },
  error: { alignSelf: 'center', paddingHorizontal: 16, paddingVertical: 4 },
  footerWrap: {
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
  },
});
