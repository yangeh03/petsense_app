import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import {
  createExerciseDiary,
  listExerciseDiaries,
  type ExerciseDiary,
} from '@/services/exercise-diary';

export function ExerciseDiaryEntry({ petId, hasPhoto }: { petId: number; hasPhoto: boolean }) {
  const theme = useTheme();
  const router = useRouter();
  const [latest, setLatest] = useState<ExerciseDiary>();
  const [hasHistory, setHasHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submitting = useRef(false);

  useFocusEffect(
    useCallback(() => {
      const controller = new AbortController();
      void listExerciseDiaries(petId, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return;
          setHasHistory(result.jobs.length > 0);
          setLatest(result.jobs.find((job) => job.date === result.date && job.mode === 'ai'));
        })
        .catch(() => {});
      return () => controller.abort();
    }, [petId]),
  );

  async function open() {
    if (submitting.current) return;
    if (!hasPhoto && !latest) {
      router.push('/pet');
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      const job = await createExerciseDiary(petId);
      setLatest(job);
      router.push({ pathname: '/exercise-diary', params: { petId, jobId: job.jobId } });
    } catch (error) {
      setError(error instanceof Error ? error.message : '暂时无法提交，请再试一次');
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  const label =
    latest?.status === 'completed'
      ? '查看今日运动日记'
      : latest?.status === 'failed'
        ? '查看日记生成结果'
        : latest
          ? '查看日记进度'
          : hasPhoto
            ? '生成今日 AI 运动日记'
            : '添加宠物照片后生成日记';
  return (
    <View style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
      <Pressable
        accessibilityRole="button"
        disabled={busy}
        accessibilityState={{ busy }}
        onPress={() => void open()}
        style={[styles.button, { backgroundColor: theme.tintBackground }]}
      >
        <ThemedText type="smallBold">{busy ? '正在提交…' : label}</ThemedText>
      </Pressable>
      {!latest && hasPhoto && (
        <ThemedText type="small" themeColor="textSecondary">
          照片和运动数据将发送给 PetGIF 生成海报，素材链接可公开访问。
        </ThemedText>
      )}
      {hasHistory && !latest && (
        <Pressable
          accessibilityRole="button"
          onPress={() => router.push({ pathname: '/exercise-diary', params: { petId } })}
        >
          <ThemedText type="small">查看以往日记</ThemedText>
        </Pressable>
      )}
      {error ? (
        <ThemedText accessibilityLiveRegion="polite" type="small">
          {error}
        </ThemedText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { padding: Spacing.three, borderRadius: 20, gap: Spacing.two },
  button: { alignItems: 'center', padding: Spacing.three, borderRadius: 14 },
});
