import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  type ScrollViewInstance,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  TextInput,
  View,
} from 'react-native';
import { useRouter, useFocusEffect } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ThemedText } from '@/components/themed-text';
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
  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: theme.background, paddingTop: insets.top }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={{ padding: 20 }}>
        <ThemedText type="title">伙伴</ThemedText>
        <ThemedText>{pet ? `你 · ${pet.name} · 照料助手` : '一起慢慢认识它的日常'}</ThemedText>
      </View>
      {loadedFor === user?.id && learningDays !== null ? (
        <View style={{ paddingHorizontal: 20 }}>
          <ThemedText>
            慢慢认识它的日常 · 已积累 {Math.min(7, learningDays)} / 7 个有效动作观测日
          </ThemedText>
          <ThemedText>声音观察 {Math.min(7, soundDays)} / 7 天 · 区域观察等待接入</ThemedText>
          <ThemedText>目前先聊实际记录，个人日常参照仍在建立。</ThemedText>
        </View>
      ) : null}
      {!user ? (
        <Pressable onPress={() => router.push('/login')}>
          <ThemedText>登录后，和它们聊聊</ThemedText>
        </Pressable>
      ) : null}
      <ScrollView
        ref={scroll}
        scrollEventThrottle={100}
        onContentSizeChange={() => {
          if (!restored.current && loadedFor === ownerId && turns.length) {
            scroll.current?.scrollTo({ y: restoreOffset.current, animated: false });
            restored.current = true;
          }
        }}
        onScroll={(event) => {
          const entry = drafts.get(ownerId);
          if (entry && restored.current)
            drafts.set(ownerId, { ...entry, scrollY: event.nativeEvent.contentOffset.y });
        }}
        contentContainerStyle={{ padding: 20, gap: 18 }}
      >
        {pet && turns.length >= 40 ? (
          <Pressable
            onPress={() => {
              void fetchAgentTurns(pet.id, turns[0].id)
                .then((data) => setTurns((current) => [...data.turns, ...current]))
                .catch(() => setError('更早的聊天暂时没取回来'));
            }}
          >
            <ThemedText style={{ color: theme.tint }}>更早的聊天</ThemedText>
          </Pressable>
        ) : null}
        {!turns.length && pet ? (
          <ThemedText>想对它说点什么？也可以问问照料助手最近的记录。</ThemedText>
        ) : null}
        {(loadedFor === user?.id
          ? turns.filter((turn) => topic === undefined || (turn.topicId ?? turn.id) === topic)
          : []
        ).map((turn) => (
          <View key={turn.id} style={{ gap: 12 }}>
            <ThemedText style={{ textAlign: 'right' }}>你：{turn.text}</ThemedText>
            <ThemedText style={{ color: theme.textSecondary }}>
              {turn.speaker === 'pet' ? `🐾 ${pet?.name ?? '爱宠'}` : '照料助手'}
            </ThemedText>
            <ThemedText>{turn.reply.replace(/^【[^】]+】/, '') || '正在接话……'}</ThemedText>
            <Pressable
              onPress={() => {
                setTopic(turn.topicId ?? turn.id);
                setReplyTo(turn.id);
              }}
            >
              <ThemedText style={{ color: theme.tint }}>回复这件事</ThemedText>
            </Pressable>
            {turn.status === 'failed' ? (
              <Pressable onPress={() => setDraft(turn.text)}>
                <ThemedText style={{ color: theme.tint }}>再说一次</ThemedText>
              </Pressable>
            ) : null}
            {turn.sources.map((source, index) => (
              <Pressable
                key={index}
                onPress={() =>
                  router.push({
                    pathname: '/records',
                    params: { petId: pet?.id, start: source.startAt, end: source.endAt },
                  })
                }
              >
                <ThemedText style={{ color: theme.tint }}>查看这段记录</ThemedText>
              </Pressable>
            ))}
          </View>
        ))}
        {busy ? <ActivityIndicator color={theme.tint} /> : null}
      </ScrollView>
      {error ? (
        <Pressable onPress={() => void reload()}>
          <ThemedText>{error}</ThemedText>
        </Pressable>
      ) : null}
      <View style={{ padding: 16, gap: 12 }}>
        {topic !== undefined ? (
          <Pressable
            onPress={() => {
              setTopic(undefined);
              setReplyTo(undefined);
            }}
          >
            <ThemedText>正在说这件事 · 返回大家的聊天</ThemedText>
          </Pressable>
        ) : null}
        <View style={{ flexDirection: 'row', gap: 20 }}>
          {(['auto', 'pet', 'assistant'] as const).map((value) => (
            <Pressable
              key={value}
              onPress={() => setTarget(value)}
              accessibilityRole="button"
              accessibilityState={{ selected: target === value }}
            >
              <ThemedText style={{ color: target === value ? theme.tint : theme.text }}>
                {value === 'auto'
                  ? '说给大家听'
                  : value === 'pet'
                    ? `@${pet?.name ?? '爱宠'}`
                    : '@照料助手'}
              </ThemedText>
            </Pressable>
          ))}
        </View>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          multiline
          maxLength={4000}
          placeholder="今天想聊些什么……"
          placeholderTextColor={theme.textSecondary}
          style={{ color: theme.text, minHeight: 48 }}
          accessibilityLabel="给伙伴的消息"
        />
        <Pressable
          disabled={busy || !pet || !draft.trim()}
          onPress={() => void send()}
          accessibilityRole="button"
        >
          <ThemedText style={{ color: theme.tint }}>发送</ThemedText>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}
