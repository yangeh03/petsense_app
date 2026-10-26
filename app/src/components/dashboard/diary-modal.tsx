import AsyncStorage from '@react-native-async-storage/async-storage';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { SymbolView } from 'expo-symbols';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { absoluteUrl } from '@/services/api';
import { validateDiaryDraft } from '@/services/diary-validation';
import {
  createDiaryJob,
  getDiaryJob,
  listDiaryJobs,
  shareDiaryPost,
  type DiaryInput,
  type DiaryJobStatus,
  type PetgifDiaryJob,
} from '@/services/petgif';

const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const ALLOWED_PHOTO_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

export function DiaryModal({
  visible,
  onClose,
  petName,
  petId,
  userId,
  petgifId,
  activeMinutes,
  onShared,
}: {
  visible: boolean;
  onClose: () => void;
  petName: string;
  petId: number;
  userId: number;
  petgifId: string | null;
  activeMinutes: number;
  onShared: () => void;
}) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const [durationMin, setDurationMin] = useState(
    activeMinutes > 0 && activeMinutes <= 600 ? String(activeMinutes) : '',
  );
  const [steps, setSteps] = useState('');
  const [places, setPlaces] = useState('');
  const [notes, setNotes] = useState('');
  const [photos, setPhotos] = useState<NonNullable<DiaryInput['photos']>>([]);
  const [jobs, setJobs] = useState<PetgifDiaryJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [submissionError, setSubmissionError] = useState('');
  const [sharing, setSharing] = useState(false);
  const [submittingState, setSubmittingState] = useState(false);
  const submitting = useRef(false);
  const storageKey = `petsense.diary.pending.${userId}.${petId}`;
  const selectedJob = useMemo(
    () => jobs.find((job) => job.jobId === selectedJobId) ?? null,
    [jobs, selectedJobId],
  );
  const selectedStatus = selectedJob?.status;
  const blockingJob = jobs.find((job) => ['queued', 'running', 'unknown'].includes(job.status));

  useEffect(() => {
    if (!visible) return;
    let active = true;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!active) return;
      setLoadingHistory(true);
      setHistoryError('');
    });
    void (async () => {
      try {
        const savedKey = await AsyncStorage.getItem(storageKey);
        if (!active) return;
        setPendingKey(savedKey);
        const history = await listDiaryJobs(petId, controller.signal);
        if (!active) return;
        setJobs(history.jobs.slice(0, 20));
        setHistoryError('');
        setSelectedJobId((current) => {
          if (current && history.jobs.some((job) => job.jobId === current)) return current;
          return (
            history.jobs.find((job) => ['queued', 'running', 'unknown'].includes(job.status))
              ?.jobId ??
            history.jobs[0]?.jobId ??
            null
          );
        });
        setSubmissionError('');
      } catch {
        if (active) setHistoryError('日记记录暂时无法加载，请检查网络后重试。');
      } finally {
        if (active) setLoadingHistory(false);
      }
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, [visible, petId, storageKey]);

  useEffect(() => {
    if (!visible || !selectedJobId || !['queued', 'running'].includes(selectedStatus ?? '')) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const updated = await getDiaryJob(selectedJobId, controller.signal);
        if (!active) return;
        setJobs((current) => current.map((job) => (job.jobId === updated.jobId ? updated : job)));
        if (['queued', 'running'].includes(updated.status)) {
          const delay = Math.min(15_000, 1_200 * 2 ** Math.min(attempt++, 4));
          timer = setTimeout(() => void poll(), delay);
        }
      } catch {
        if (!active || controller.signal.aborted) return;
        setSubmissionError('暂时无法更新进度，任务仍保存在账号中。');
        timer = setTimeout(
          () => void poll(),
          Math.min(15_000, 2_000 * 2 ** Math.min(attempt++, 3)),
        );
      }
    };
    timer = setTimeout(() => void poll(), 900);
    return () => {
      active = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [visible, selectedJobId, selectedStatus]);

  const refreshHistory = async () => {
    setHistoryError('');
    try {
      const savedKey = await AsyncStorage.getItem(storageKey);
      setPendingKey(savedKey);
      const result = await listDiaryJobs(petId);
      setJobs(result.jobs.slice(0, 20));
    } catch {
      setHistoryError('日记记录暂时无法加载，请重试。');
    }
  };

  const pickPhotos = async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        quality: 0.8,
        allowsMultipleSelection: true,
        selectionLimit: 3,
      });
      if (result.canceled) return;
      const assets = result.assets ?? [];
      if (assets.length > 3) {
        Alert.alert('照片数量超限', '一次最多添加 3 张照片。');
        return;
      }
      const invalid = assets.find(
        (asset) =>
          (asset.fileSize != null && asset.fileSize > MAX_PHOTO_BYTES) ||
          !ALLOWED_PHOTO_TYPES.has(asset.mimeType ?? 'image/jpeg'),
      );
      if (invalid) {
        Alert.alert('照片无法使用', '请选择 JPG、PNG 或 WebP 图片，每张不超过 10MB。');
        return;
      }
      setPhotos(
        assets.map((asset) => ({
          uri: asset.uri,
          name: asset.fileName ?? 'walk.jpg',
          type: asset.mimeType ?? 'image/jpeg',
        })),
      );
    } catch {
      Alert.alert('无法打开相册', '请检查照片访问权限后重试。');
    }
  };

  const generate = async () => {
    if (!petgifId || submitting.current || blockingJob || loadingHistory || historyError) return;
    const validated = validateDiaryDraft({ durationMin, steps, places, notes });
    if (!validated) {
      Alert.alert(
        '请检查运动记录',
        '时长需为 1—600 分钟，步数需为 0—100000；地点和备注最多 200 字。',
      );
      return;
    }
    submitting.current = true;
    setSubmittingState(true);
    setSubmissionError('');
    const key = pendingKey ?? makeIdempotencyKey();
    setPendingKey(key);
    try {
      await AsyncStorage.setItem(storageKey, key);
      const job = await createDiaryJob(
        petId,
        {
          ...validated,
          photos,
        },
        key,
      );
      setJobs((current) =>
        [job, ...current.filter((item) => item.jobId !== job.jobId)].slice(0, 20),
      );
      setSelectedJobId(job.jobId);
      setPhotos([]);
      setPendingKey(null);
      await AsyncStorage.removeItem(storageKey);
    } catch {
      setSubmissionError('暂时没有收到服务端确认。再次确认会复用同一请求号，不会创建重复日记。');
    } finally {
      submitting.current = false;
      setSubmittingState(false);
    }
  };

  const queryUnknownJob = async () => {
    if (!selectedJob || selectedJob.status !== 'unknown') return;
    try {
      const updated = await getDiaryJob(selectedJob.jobId);
      setJobs((current) => current.map((job) => (job.jobId === updated.jobId ? updated : job)));
      setSubmissionError('');
    } catch {
      setSubmissionError('暂时无法查询任务，请稍后再试。');
    }
  };

  const share = async () => {
    if (!selectedJob?.posterUrl || sharing) return;
    setSharing(true);
    setSubmissionError('');
    try {
      await shareDiaryPost({
        posterUrl: selectedJob.posterUrl,
        caption: `${petName} 的运动日记 🏃 ${selectedJob.input?.durationMin ?? '运动'} 分钟${
          selectedJob.input?.places ? ` · ${selectedJob.input.places}` : ''
        }`,
        location: selectedJob.input?.places ?? '',
        petId,
      });
      onShared();
      onClose();
    } catch {
      setSubmissionError('分享失败，生成的海报仍保存在日记记录中，可以再次分享。');
    } finally {
      setSharing(false);
    }
  };

  const posterUrl = selectedJob?.status === 'completed' ? selectedJob.posterUrl : null;

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View
        style={[
          styles.sheet,
          { backgroundColor: theme.background, paddingTop: insets.top + Spacing.three },
        ]}
      >
        <View style={styles.header}>
          <ThemedText type="subtitle" style={styles.title}>
            运动日记
          </ThemedText>
          <Pressable onPress={onClose} hitSlop={10} accessibilityLabel="关闭">
            <SymbolView
              name={{ ios: 'xmark.circle.fill', android: 'cancel', web: 'cancel' }}
              size={26}
              tintColor={theme.textSecondary}
            />
          </Pressable>
        </View>
        <ScrollView
          contentContainerStyle={[styles.form, { paddingBottom: insets.bottom + Spacing.five }]}
        >
          {!petgifId ? (
            <View style={[styles.locked, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">预设动画仅用于首页展示</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                运动日记需要你自己的 PetGIF
                数字形象。绑定个人形象后即可创建，不会使用共享预设的记录。
              </ThemedText>
            </View>
          ) : (
            <>
              <ThemedText type="small" themeColor="textSecondary" style={styles.guide}>
                用你确认的运动记录，为 {petName}{' '}
                制作日记海报。项圈步数不可用时请手动填写；生成通常需要约 2 分钟。
              </ThemedText>
              <View style={styles.statsRow}>
                <View style={[styles.statBox, { borderColor: theme.backgroundSelected }]}>
                  <ThemedText type="small" themeColor="textSecondary">
                    活跃（分钟）
                  </ThemedText>
                  <TextInput
                    value={durationMin}
                    onChangeText={setDurationMin}
                    keyboardType="number-pad"
                    accessibilityLabel="运动时长分钟"
                    style={[styles.input, { color: theme.text }]}
                    maxLength={3}
                  />
                </View>
                <View style={[styles.statBox, { borderColor: theme.backgroundSelected }]}>
                  <ThemedText type="small" themeColor="textSecondary">
                    手动估计步数
                  </ThemedText>
                  <TextInput
                    value={steps}
                    onChangeText={setSteps}
                    keyboardType="number-pad"
                    accessibilityLabel="运动步数"
                    style={[styles.input, { color: theme.text }]}
                    maxLength={6}
                  />
                </View>
              </View>
              <Pressable
                onPress={() => void pickPhotos()}
                disabled={loadingHistory || !!historyError || !!blockingJob}
                style={styles.photoRow}
              >
                <SymbolView
                  name={{
                    ios: 'photo.on.rectangle.angled',
                    android: 'photo_library',
                    web: 'photo_library',
                  }}
                  size={18}
                  tintColor={theme.tint}
                />
                <ThemedText type="small" style={styles.photoRowText}>
                  {photos.length
                    ? `已选 ${photos.length} 张遛弯照片`
                    : '添加遛弯照片（可选，无照片使用个人形象图）'}
                </ThemedText>
                {photos.map((photo) => (
                  <Image
                    key={photo.uri}
                    source={{ uri: photo.uri }}
                    style={styles.photoThumb}
                    contentFit="cover"
                  />
                ))}
              </Pressable>
              <TextInput
                value={places}
                onChangeText={setPlaces}
                placeholder="地点（选填）"
                placeholderTextColor={theme.textSecondary}
                style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
                maxLength={200}
              />
              <TextInput
                value={notes}
                onChangeText={setNotes}
                placeholder="备注（选填）"
                placeholderTextColor={theme.textSecondary}
                style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
                maxLength={200}
              />
              {selectedJob && <JobStatus job={selectedJob} />}
              {posterUrl ? (
                <View style={[styles.poster, { borderColor: theme.backgroundSelected }]}>
                  <Image
                    source={{ uri: absoluteUrl(posterUrl) }}
                    style={styles.posterImage}
                    contentFit="contain"
                    cachePolicy="memory-disk"
                    transition={120}
                  />
                </View>
              ) : null}
              {!!submissionError && (
                <ThemedText type="small" style={{ color: theme.danger }}>
                  {submissionError}
                </ThemedText>
              )}
              {selectedJob?.status === 'unknown' ? (
                <Pressable
                  onPress={() => void queryUnknownJob()}
                  style={[styles.primaryButton, { backgroundColor: theme.tint }]}
                >
                  <ThemedText type="smallBold" style={styles.buttonText}>
                    查询任务状态
                  </ThemedText>
                </Pressable>
              ) : (
                <Pressable
                  onPress={() => void generate()}
                  disabled={!!blockingJob || loadingHistory || !!historyError}
                  style={[
                    styles.primaryButton,
                    {
                      backgroundColor: theme.tint,
                      opacity: blockingJob || loadingHistory || historyError ? 0.5 : 1,
                    },
                  ]}
                >
                  {submittingState ? (
                    <ActivityIndicator color="#ffffff" />
                  ) : (
                    <ThemedText type="smallBold" style={styles.buttonText}>
                      {pendingKey
                        ? '确认上次提交状态'
                        : selectedJob?.status === 'failed'
                          ? '重新生成日记'
                          : '生成运动日记'}
                    </ThemedText>
                  )}
                </Pressable>
              )}
              {posterUrl && (
                <Pressable
                  onPress={() => void share()}
                  disabled={sharing}
                  style={[styles.primaryButton, { backgroundColor: theme.tint }]}
                >
                  {sharing ? (
                    <ActivityIndicator color="#ffffff" />
                  ) : (
                    <ThemedText type="smallBold" style={styles.buttonText}>
                      分享到社区
                    </ThemedText>
                  )}
                </Pressable>
              )}
            </>
          )}
          <View style={styles.historyHeader}>
            <ThemedText type="smallBold">最近日记</ThemedText>
            <Pressable accessibilityRole="button" onPress={() => void refreshHistory()}>
              <ThemedText type="small" style={{ color: theme.tint }}>
                刷新
              </ThemedText>
            </Pressable>
          </View>
          {loadingHistory ? <ActivityIndicator color={theme.tint} /> : null}
          {!!historyError && (
            <ThemedText type="small" style={{ color: theme.danger }}>
              {historyError}
            </ThemedText>
          )}
          {!loadingHistory && jobs.length === 0 && (
            <ThemedText type="small" themeColor="textSecondary">
              完成的日记会保存在这里。
            </ThemedText>
          )}
          {jobs.map((job) => (
            <Pressable
              key={job.jobId}
              onPress={() => setSelectedJobId(job.jobId)}
              accessibilityRole="button"
              accessibilityState={{ selected: selectedJobId === job.jobId }}
              style={[
                styles.historyItem,
                {
                  borderColor: selectedJobId === job.jobId ? theme.tint : theme.backgroundSelected,
                },
              ]}
            >
              <ThemedText type="smallBold">{statusLabel(job.status)}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {new Date(job.createdAt).toLocaleString()}
              </ThemedText>
              {job.posterUrl ? (
                <ThemedText type="small" style={{ color: theme.tint }}>
                  查看海报
                </ThemedText>
              ) : null}
            </Pressable>
          ))}
        </ScrollView>
      </View>
    </Modal>
  );
}

function JobStatus({ job }: { job: PetgifDiaryJob }) {
  const theme = useTheme();
  return (
    <View
      accessibilityLiveRegion="polite"
      style={[styles.jobStatus, { backgroundColor: theme.backgroundElement }]}
    >
      {(job.status === 'queued' || job.status === 'running') && (
        <ActivityIndicator color={theme.tint} size="small" />
      )}
      <View style={styles.jobText}>
        <ThemedText type="smallBold">{statusLabel(job.status)}</ThemedText>
        {job.message ? (
          <ThemedText type="small" themeColor="textSecondary">
            {job.message}
          </ThemedText>
        ) : null}
      </View>
    </View>
  );
}

function statusLabel(status: DiaryJobStatus) {
  return {
    queued: '排队中',
    running: '正在生成',
    completed: '已完成',
    failed: '生成失败',
    unknown: '状态待确认',
  }[status];
}

function makeIdempotencyKey() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

const styles = StyleSheet.create({
  sheet: { flex: 1, alignItems: 'center' },
  header: {
    width: '100%',
    maxWidth: 640,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Spacing.four,
    paddingBottom: Spacing.three,
  },
  title: { fontSize: 22 },
  form: { width: '100%', maxWidth: 640, paddingHorizontal: Spacing.four, gap: Spacing.three },
  guide: { textAlign: 'center' },
  locked: { borderRadius: 18, padding: Spacing.four, gap: Spacing.two },
  statsRow: { flexDirection: 'row', gap: Spacing.three },
  statBox: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 8,
    gap: 4,
  },
  input: {
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: Spacing.three,
    paddingVertical: 10,
    fontSize: 14,
  },
  photoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
    borderWidth: 1,
    borderRadius: 14,
    borderColor: 'rgba(148,131,111,0.3)',
    paddingHorizontal: Spacing.three,
    paddingVertical: 10,
  },
  photoRowText: { flex: 1, flexShrink: 1 },
  photoThumb: { width: 28, height: 28, borderRadius: 6 },
  poster: { borderRadius: 18, borderWidth: 1, overflow: 'hidden', aspectRatio: 3 / 4 },
  posterImage: { width: '100%', height: '100%' },
  primaryButton: { borderRadius: 16, alignItems: 'center', paddingVertical: 12 },
  buttonText: { color: '#ffffff' },
  jobStatus: {
    borderRadius: 14,
    padding: Spacing.three,
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  jobText: { flex: 1, gap: 2 },
  historyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: Spacing.two,
  },
  historyItem: { borderWidth: 1, borderRadius: 14, padding: Spacing.three, gap: 3 },
});
