import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  type ScrollViewInstance,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { SymbolView } from 'expo-symbols';
import { useRouter, useFocusEffect } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ThemedText } from '@/components/themed-text';
import { BottomTabInset, Fonts, MaxContentWidth, Shadows } from '@/constants/theme';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import { fetchDashboard } from '@/services/api';
import {
  fetchAgentTurns,
  sendAgentTurn,
  fetchAgentObservation,
  markAgentRead,
  type AgentTurn,
} from '@/services/agent';

type PendingRequest = {
  text: string;
  target: AgentTurn['target'];
  pet: number;
  key: string;
  topic?: number;
  replyTo?: number;
};
const drafts = new Map<
  number,
  {
    text: string;
    target: AgentTurn['target'];
    topic?: number;
    replyTo?: number;
    turns?: AgentTurn[];
    scrollY?: number;
    pending?: PendingRequest;
  }
>();
export default function CompanionScreen() {
  const { user } = useAuth();
  return <CompanionContent key={user?.id ?? 'guest'} ownerId={user?.id ?? 0} />;
}
function CompanionContent({ ownerId }: { ownerId: number }) {
  const cached = drafts.get(ownerId);
  const scroll = useRef<ScrollViewInstance>(null);
  const restored = useRef(false);
  const restoreOffset = useRef(cached?.scrollY ?? 0);
  const theme = useTheme();
  const { user } = useAuth();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const pending = useRef<PendingRequest | null>(cached?.pending ?? null);
  const [loadedFor, setLoadedFor] = useState<number | null>(cached ? ownerId : null);
  const [pet, setPet] = useState<{ id: number; name: string } | null>(null);
  const [turns, setTurns] = useState<AgentTurn[]>(cached?.turns ?? []);
  const [topic, setTopic] = useState<number | undefined>(cached?.topic);
  const [replyTo, setReplyTo] = useState<number | undefined>(cached?.replyTo);
  const [draft, setDraft] = useState(cached?.text ?? '');
  const [target, setTarget] = useState<AgentTurn['target']>(cached?.target ?? 'auto');
  const [learningDays, setLearningDays] = useState<number | null>(null);
  const [soundDays, setSoundDays] = useState(0);
  const [showLearning, setShowLearning] = useState(false);
  const [showTargets, setShowTargets] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const followReply = useRef(false);
  useEffect(() => {
    const show = Keyboard.addListener('keyboardWillShow', () => setKeyboardOpen(true));
    const hide = Keyboard.addListener('keyboardWillHide', () => setKeyboardOpen(false));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    drafts.set(ownerId, {
      ...drafts.get(ownerId),
      text: draft,
      target,
      topic,
      replyTo,
      turns,
      pending: pending.current ?? undefined,
    });
  }, [ownerId, draft, target, topic, replyTo, turns, busy]);
  const reload = useCallback(async () => {
    await Promise.resolve();
    if (!user) {
      setTurns([]);
      setPet(null);
      return;
    }
    try {
      const data = await fetchDashboard();
      setPet(data.pet);
      if (data.pet) {
        const latest = (await fetchAgentTurns(data.pet.id)).turns;
        setTurns((current) => [
          ...current.filter((turn) => latest.length > 0 && turn.id < latest[0].id),
          ...latest,
        ]);
        const end = new Date();
        const start = new Date(end.getTime() - 89 * 86400000);
        const facts = await fetchAgentObservation(
          data.pet.id,
          start.toISOString(),
          end.toISOString(),
        );
        setLearningDays(facts.learning.validDays);
        setSoundDays(
          facts.learningDimensions.find((value) => value.dimension === 'sounds')?.validDays ?? 0,
        );
      }
      setLoadedFor(user.id);
      setError('');
    } catch {
      setError('暂时没能取回我们的聊天，点这里重试');
    }
  }, [user]);
  useEffect(() => {
    void Promise.resolve().then(reload);
  }, [reload]);
  useEffect(() => {
    if (!pet || !user || (!busy && !turns.some((turn) => turn.status === 'pending'))) return;
    let active = true;
    const timer = setInterval(() => {
      void fetchAgentTurns(pet.id)
        .then((data) => {
          if (active) setTurns(data.turns);
        })
        .catch(() => {});
    }, 800);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [pet, user, busy, turns]);
  useFocusEffect(
    useCallback(() => {
      if (!pet || loadedFor !== ownerId) return;
      const ids = turns
        .filter(
          (turn) =>
            turn.status !== 'pending' &&
            !turn.readAt &&
            (topic === undefined || (turn.topicId ?? turn.id) === topic),
        )
        .slice(0, 40)
        .map((turn) => turn.id);
      if (!ids.length) return;
      let active = true;
      void markAgentRead(pet.id, ids)
        .then((result) => {
          if (active)
            setTurns((current) =>
              current.map((turn) => result.turns.find((read) => read.id === turn.id) ?? turn),
            );
        })
        .catch(() => {});
      return () => {
        active = false;
      };
    }, [pet, loadedFor, ownerId, turns, topic]),
  );
  const send = async () => {
    if (!pet || !draft.trim() || busy) return;
    if (
      !pending.current ||
      pending.current.text !== draft.trim() ||
      pending.current.target !== target ||
      pending.current.pet !== pet.id ||
      pending.current.topic !== topic ||
      pending.current.replyTo !== replyTo
    ) {
      pending.current = {
        text: draft.trim(),
        target,
        pet: pet.id,
        topic,
        replyTo,
        key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      };
    }
    followReply.current = true;
    setShowTargets(false);
    setBusy(true);
    setError('');
    try {
      const result = await sendAgentTurn(
        pet.id,
        draft.trim(),
        target,
        pending.current.key,
        topic,
        replyTo,
      );
      setTurns((old) => [...old.filter((item) => item.id !== result.turn.id), result.turn]);
      pending.current = null;
      setDraft('');
      setReplyTo(undefined);
    } catch {
      setError('暂时没能确认送达，原话已保留，可以重试');
    } finally {
      setBusy(false);
    }
  };
  const visible =
    loadedFor === user?.id
      ? turns.filter((turn) => topic === undefined || (turn.topicId ?? turn.id) === topic)
      : [];
  const canSend = Boolean(pet && draft.trim() && !busy);
  const targetLabel =
    target === 'pet' ? (pet?.name ?? '爱宠') : target === 'assistant' ? '照料助手' : '大家';

  return (
    <KeyboardAvoidingView
      style={{
        flex: 1,
        backgroundColor: theme.background,
        paddingTop: insets.top,
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
          {pet ? (
            <ThemedText type="small" themeColor="textSecondary">
              你 · {pet.name} · 照料助手
            </ThemedText>
          ) : null}
        </View>
        {loadedFor === user?.id && learningDays !== null ? (
          <Pressable
            onPress={() => setShowLearning(!showLearning)}
            accessibilityRole="button"
            accessibilityState={{ expanded: showLearning }}
            style={[styles.pill, { backgroundColor: theme.backgroundSelected }]}
          >
            <ThemedText type="smallBold">认识中 {Math.min(7, learningDays)}/7</ThemedText>
          </Pressable>
        ) : null}
      </View>
      {showLearning && learningDays !== null ? (
        <View style={[styles.learning, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText type="smallBold">慢慢认识它的日常</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            动作 {Math.min(7, learningDays)}/7 天 · 声音 {Math.min(7, soundDays)}/7 天
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            区域观察等待接入，个人日常参照仍在建立。
          </ThemedText>
        </View>
      ) : null}
      {topic !== undefined ? (
        <Pressable
          style={[styles.topic, { backgroundColor: theme.tintBackground }]}
          accessibilityRole="button"
          accessibilityLabel="返回大家的聊天"
          onPress={() => {
            setTopic(undefined);
            setReplyTo(undefined);
          }}
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
          if (!restored.current && loadedFor === ownerId && turns.length && !followReply.current) {
            scroll.current?.scrollTo({ y: restoreOffset.current, animated: false });
            restored.current = true;
          } else if (followReply.current) {
            scroll.current?.scrollToEnd({ animated: false });
            restored.current = true;
            if (!busy) followReply.current = false;
          }
        }}
        onScrollBeginDrag={() => {
          followReply.current = false;
        }}
        onScroll={(event) => {
          const entry = drafts.get(ownerId);
          if (entry && restored.current)
            drafts.set(ownerId, { ...entry, scrollY: event.nativeEvent.contentOffset.y });
        }}
        contentContainerStyle={[styles.feed, !visible.length && styles.emptyFeed]}
      >
        {pet && turns.length >= 40 ? (
          <Pressable
            style={styles.center}
            accessibilityRole="button"
            onPress={() => {
              void fetchAgentTurns(pet.id, turns[0].id)
                .then((data) => setTurns((current) => [...data.turns, ...current]))
                .catch(() => setError('更早的聊天暂时没取回来'));
            }}
          >
            <ThemedText type="smallBold" themeColor="textSecondary">
              更早的聊天
            </ThemedText>
          </Pressable>
        ) : null}
        {!visible.length && !busy && (!user || pet) ? (
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
              {pet ? `今天想聊聊${pet.name}的什么？` : '和它聊聊今天'}
            </ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              {pet
                ? '和它打个招呼，或请照料助手看看最近的记录。'
                : '登录后，就能和爱宠及照料助手聊天。'}
            </ThemedText>
            {pet ? (
              <View style={styles.suggestions}>
                {['它昨天走动多吗？', '@爱宠 打个招呼'].map((idea, index) => (
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
                    <ThemedText type="smallBold">
                      {index === 0 ? idea : `和${pet.name}打招呼`}
                    </ThemedText>
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
          <View key={turn.id} style={styles.exchange}>
            <View style={[styles.ownerBubble, { backgroundColor: theme.tintBackground }]}>
              <ThemedText>{turn.text}</ThemedText>
            </View>
            <View style={styles.replyRow}>
              <View
                style={[
                  styles.avatar,
                  {
                    backgroundColor:
                      turn.speaker === 'pet' ? theme.tintBackground : theme.backgroundSelected,
                  },
                ]}
              >
                {turn.speaker === 'pet' ? (
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
                  {turn.speaker === 'pet' ? (pet?.name ?? '爱宠') : '照料助手'}
                </ThemedText>
                <ThemedText>{turn.reply.replace(/^【[^】]+】/, '') || '正在接话…'}</ThemedText>
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
                        params: { petId: pet?.id, start: source.startAt, end: source.endAt },
                      })
                    }
                  >
                    <SymbolView
                      name={{ ios: 'chart.bar', android: 'bar_chart', web: 'bar_chart' }}
                      tintColor={theme.tint}
                      size={16}
                    />
                    <ThemedText type="smallBold" style={styles.heading}>
                      查看这段记录
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
                  </Pressable>
                ))}
                {turn.status !== 'pending' ? (
                  <Pressable
                    accessibilityRole="button"
                    style={styles.replyAction}
                    onPress={() => {
                      if (turn.status === 'failed') {
                        setDraft(turn.text);
                        setTarget(turn.target);
                      } else {
                        setTopic(turn.topicId ?? turn.id);
                        setReplyTo(turn.id);
                      }
                    }}
                  >
                    <ThemedText type="small" themeColor="textSecondary">
                      {turn.status === 'failed' ? '再说一次' : '继续聊这件事'}
                    </ThemedText>
                  </Pressable>
                ) : null}
              </View>
            </View>
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
      {error ? (
        <Pressable style={styles.error} accessibilityRole="button" onPress={() => void reload()}>
          <ThemedText type="small" themeColor="danger">
            {error}
          </ThemedText>
        </Pressable>
      ) : null}
      {pet ? (
        <View
          style={[
            styles.footer,
            Platform.OS === 'ios' &&
              !keyboardOpen && {
                marginBottom: BottomTabInset + insets.bottom,
              },
          ]}
        >
          {showTargets ? (
            <View style={styles.targets}>
              {(['auto', 'pet', 'assistant'] as const).map((value) => (
                <Pressable
                  key={value}
                  accessibilityRole="button"
                  accessibilityState={{ selected: target === value }}
                  style={[
                    styles.pill,
                    {
                      backgroundColor:
                        target === value ? theme.tintBackground : theme.backgroundSelected,
                    },
                  ]}
                  onPress={() => {
                    setTarget(value);
                    setShowTargets(false);
                  }}
                >
                  <ThemedText type="smallBold">
                    {value === 'auto' ? '大家' : value === 'pet' ? pet.name : '照料助手'}
                  </ThemedText>
                </Pressable>
              ))}
            </View>
          ) : null}
          <View
            style={[
              styles.composer,
              { backgroundColor: theme.backgroundElement, borderColor: theme.backgroundSelected },
            ]}
          >
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`选择发言对象，当前${targetLabel}`}
              accessibilityState={{ expanded: showTargets }}
              style={[styles.targetButton, { backgroundColor: theme.backgroundSelected }]}
              onPress={() => setShowTargets(!showTargets)}
            >
              <ThemedText type="smallBold">{target === 'auto' ? '@' : targetLabel}</ThemedText>
            </Pressable>
            <TextInput
              value={draft}
              onChangeText={setDraft}
              multiline
              maxLength={4000}
              editable={!busy}
              placeholder={topic !== undefined ? '继续说这件事…' : '说给伙伴听…'}
              placeholderTextColor={theme.textSecondary}
              style={[styles.input, { color: theme.text }]}
              accessibilityLabel="给伙伴的消息"
            />
            <Pressable
              disabled={!canSend}
              onPress={() => void send()}
              accessibilityRole="button"
              accessibilityLabel="发送"
              accessibilityState={{ disabled: !canSend }}
              style={[
                styles.send,
                { backgroundColor: canSend ? theme.tint : theme.backgroundSelected },
              ]}
            >
              <SymbolView
                name={{ ios: 'arrow.up', android: 'arrow_upward', web: 'arrow_upward' }}
                tintColor={canSend ? theme.background : theme.textSecondary}
                size={20}
              />
            </Pressable>
          </View>
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
  exchange: { gap: 16 },
  ownerBubble: {
    alignSelf: 'flex-end',
    maxWidth: '85%',
    paddingHorizontal: 15,
    paddingVertical: 10,
    borderRadius: 20,
    borderBottomRightRadius: 6,
  },
  replyRow: { flexDirection: 'row', gap: 9, alignItems: 'flex-start' },
  replyBody: { flex: 1, gap: 6, paddingTop: 2 },
  source: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
    marginTop: 4,
  },
  replyAction: { alignSelf: 'flex-start', paddingVertical: 4 },
  waiting: { flexDirection: 'row', gap: 9, alignItems: 'center' },
  error: { alignSelf: 'center', paddingHorizontal: 16, paddingVertical: 4 },
  footer: {
    width: '100%',
    maxWidth: MaxContentWidth,
    alignSelf: 'center',
    paddingHorizontal: 14,
    paddingBottom: 5,
    gap: 7,
  },
  targets: { flexDirection: 'row', gap: 8, paddingLeft: 6 },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 8,
    borderWidth: 1,
    padding: 6,
    borderRadius: 24,
    boxShadow: Shadows.chip,
  },
  targetButton: {
    minWidth: 40,
    minHeight: 40,
    borderRadius: 20,
    paddingHorizontal: 9,
    justifyContent: 'center',
    alignItems: 'center',
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 108,
    paddingVertical: 9,
    fontSize: 16,
    lineHeight: 22,
  },
  send: { width: 40, height: 40, borderRadius: 20, justifyContent: 'center', alignItems: 'center' },
});
