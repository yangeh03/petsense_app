import { Image } from 'expo-image';
import { requestPermissionsAsync, saveToLibraryAsync } from 'expo-media-library/legacy';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useState, type PropsWithChildren } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { MaxContentWidth, Spacing } from '@/constants/theme';
import { getDiaryJob } from '@/services/petgif';
import { useAuth } from '@/hooks/use-auth';
import { useTheme } from '@/hooks/use-theme';
import {
  createExerciseDiary,
  downloadDiaryPoster,
  listExerciseDiaries,
  type DiaryCollection,
} from '@/services/exercise-diary';

export default function ExerciseDiaryScreen() {
  const params = useLocalSearchParams<{ petId: string; jobId?: string }>();
  const { user } = useAuth();
  return user ? (
    <DiaryContent
      key={`${user.id}:${params.petId}`}
      petId={Number(params.petId)}
      jobId={params.jobId}
    />
  ) : (
    <ThemedText>登录后查看运动日记</ThemedText>
  );
}

function DiaryContent({ petId, jobId }: { petId: number; jobId?: string }) {
  const router = useRouter();
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const [data, setData] = useState<DiaryCollection>();
  const [selectedId, setSelectedId] = useState(jobId);
  const [poster, setPoster] = useState<{ jobId: string; uri: string }>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [refresh, setRefresh] = useState(0);
  const selected = data?.jobs.find((job) => job.jobId === selectedId) ?? data?.jobs[0];
  const pending = selected?.status === 'queued' || selected?.status === 'running';
  const readyId = selected?.status === 'completed' ? selected.jobId : undefined;
  const today = data?.date === selected?.date;

  useFocusEffect(
    useCallback(() => {
      const controller = new AbortController();
      const interval = refresh === 0 ? 3500 : 3000;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const load = async () => {
        try {
          const result = await listExerciseDiaries(petId, controller.signal);
          if (controller.signal.aborted) return;
          setData(result);
          if (result.jobs.some((job) => job.status === 'queued' || job.status === 'running'))
            timer = setTimeout(() => void load(), interval);
        } catch {
          if (!controller.signal.aborted) setMessage('暂时无法读取日记，请刷新重试');
        }
      };
      void load();
      return () => {
        controller.abort();
        clearTimeout(timer);
      };
    }, [petId, refresh]),
  );

  useEffect(() => {
    if (!readyId) return;
    let alive = true;
    let uri: string | undefined;
    void downloadDiaryPoster(readyId)
      .then((result) => {
        if (!alive) {
          if (Platform.OS === 'web') URL.revokeObjectURL(result);
          return;
        }
        uri = result;
        setPoster({ jobId: readyId, uri: result });
      })
      .catch(() => {
        if (alive) setMessage('海报加载失败，请刷新重试');
      });
    return () => {
      alive = false;
      if (uri && Platform.OS === 'web') URL.revokeObjectURL(uri);
    };
  }, [readyId, refresh]);

  async function generate(replaceJobId: string) {
    setBusy(true);
    setMessage('');
    try {
      const job = await createExerciseDiary(petId, replaceJobId);
      setSelectedId(job.jobId);
      setRefresh((value) => value + 1);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '提交失败，请重试');
    } finally {
      setBusy(false);
    }
  }

  const uri = poster?.jobId === readyId ? poster?.uri : undefined;

  async function save() {
    if (!uri) return;
    setBusy(true);
    setMessage('');
    try {
      if (Platform.OS === 'web') {
        const link = document.createElement('a');
        link.href = uri;
        link.download = 'PetSense-运动日记.png';
        link.click();
        setMessage('已开始下载');
      } else {
        if (!(await requestPermissionsAsync(true)).granted) {
          setMessage('允许添加照片后即可保存海报');
          return;
        }
        await saveToLibraryAsync(uri);
        setMessage('已保存到相册');
      }
    } catch {
      setMessage('保存失败，请重试');
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScrollView
      contentInsetAdjustmentBehavior="automatic"
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + Spacing.four }]}
    >
      {!data && !message && <ThemedText>正在读取日记…</ThemedText>}
      {selected && (
        <>
          <ThemedText type="small" themeColor="textSecondary">
            {selected.date}
          </ThemedText>
          {pending ? <ThemedText>日记生成中，可以稍后回来查看</ThemedText> : null}
          {selected.status === 'failed' || selected.status === 'unknown' ? (
            <ThemedText>{selected.message || '暂时无法获取生成结果'}</ThemedText>
          ) : null}
          {selected.status === 'unknown' && (
            <Action
              onPress={() => {
                void getDiaryJob(selected.jobId)
                  .then(() => setRefresh((value) => value + 1))
                  .catch(() => setMessage('暂时无法查询，请稍后再试'));
              }}
            >
              查询生成状态
            </Action>
          )}
          {uri && readyId && (
            <>
              <Image
                source={uri}
                contentFit="contain"
                accessibilityLabel="运动日记海报"
                style={[styles.poster, { aspectRatio: selected.mode === 'ai' ? 0.56 : 0.75 }]}
              />
              <View style={styles.actions}>
                <Action onPress={() => void save()} disabled={busy}>
                  {Platform.OS === 'web' ? '下载海报' : '保存到相册'}
                </Action>
                <Action
                  onPress={() =>
                    router.push({ pathname: '/create', params: { diaryJobId: readyId } })
                  }
                >
                  去社区发布
                </Action>
              </View>
            </>
          )}
          {data?.aiAvailable &&
            today &&
            selected.mode === 'ai' &&
            (selected.status === 'completed' || selected.status === 'failed') && (
              <Action onPress={() => void generate(selected.jobId)} disabled={busy}>
                {selected.status === 'failed' ? '重试生成' : '重新生成'}
              </Action>
            )}
        </>
      )}
      {message ? (
        <>
          <ThemedText accessibilityLiveRegion="polite">{message}</ThemedText>
          <Action
            onPress={() => {
              setMessage('');
              setRefresh((value) => value + 1);
            }}
          >
            刷新日记
          </Action>
        </>
      ) : null}
      {data && data.jobs.length > 1 && (
        <>
          <ThemedText type="smallBold">近期日记</ThemedText>
          {data.jobs
            .filter((job) => job.jobId !== selected?.jobId)
            .map((job) => (
              <Action
                key={job.jobId}
                onPress={() => {
                  setMessage('');
                  setSelectedId(job.jobId);
                }}
              >
                {job.date} · {job.mode === 'ai' ? 'AI 海报' : '运动日记'}
              </Action>
            ))}
        </>
      )}
    </ScrollView>
  );
}

function Action({
  children,
  onPress,
  disabled,
}: PropsWithChildren<{ onPress: () => void; disabled?: boolean }>) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.button,
        { backgroundColor: theme.tintBackground, opacity: disabled ? 0.55 : 1 },
      ]}
    >
      <ThemedText type="smallBold" style={{ color: theme.text }}>
        {children}
      </ThemedText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  content: {
    alignSelf: 'center',
    width: '100%',
    maxWidth: MaxContentWidth,
    padding: Spacing.four,
    gap: Spacing.three,
  },
  poster: { width: '100%', borderRadius: 20 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two },
  button: { paddingHorizontal: Spacing.four, paddingVertical: Spacing.three, borderRadius: 14 },
});
