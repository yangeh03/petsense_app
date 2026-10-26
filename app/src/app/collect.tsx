import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { useKeepAwake } from 'expo-keep-awake';
import { SymbolView } from 'expo-symbols';
import { useVideoPlayer, VideoView } from 'expo-video';
import { Redirect, useRouter, useNavigation } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  AppState,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '@/hooks/use-theme';
import { useAuth } from '@/hooks/use-auth';
import { fetchMyDevice, loadToken } from '@/services/api';
import {
  api,
  bj,
  EVENTS,
  loadClips,
  newId,
  retainVideo,
  saveClip,
  synchronize,
  uploadClip,
  utcNow,
  type Clip,
  type Clock,
  type DeviceFrame,
  type Marker,
} from '@/services/collection';

function Review({ clip, token, close }: { clip: Clip; token: string; close: () => void }) {
  const theme = useTheme();
  const player = useVideoPlayer(clip.uri ?? null);
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [selected, setSelected] = useState<Marker | null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    api<{ markers: Marker[] }>(token, `/sessions/${clip.session_id}/quality`)
      .then((data) =>
        setMarkers(
          data.markers.filter(
            (marker) =>
              Number.isFinite(marker.utc_us) &&
              (!clip.calibration_marker || marker.id === clip.calibration_marker),
          ),
        ),
      )
      .catch((e) => setMessage(String(e)));
  }, [clip.session_id, clip.calibration_marker, token]);
  async function confirm() {
    if (!selected || saving) return;
    setSaving(true);
    try {
      await api(token, `/sessions/${clip.session_id}/videos/${clip.id}/alignment`, {
        boot: selected.boot,
        seq: selected.seq,
        video_ms: Math.round(player.currentTime * 1000),
      });
      setMessage('已保存画面与 IMU 样本的对应关系');
    } catch (error) {
      setMessage(String(error));
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal onRequestClose={close} animationType="slide">
      <ScrollView
        style={{ backgroundColor: theme.background }}
        contentContainerStyle={{ padding: 24, paddingTop: 64, gap: 16 }}
      >
        <Text style={{ color: theme.text, fontSize: 22, fontWeight: '700' }}>晃动帧校准</Text>
        <VideoView player={player} style={{ width: '100%', height: 260 }} nativeControls />
        <View style={styles.row}>
          {[-0.1, 0.1].map((delta) => (
            <Pressable
              key={delta}
              onPress={() => {
                player.pause();
                player.currentTime = Math.max(0, player.currentTime + delta);
              }}
              style={[styles.button, { backgroundColor: theme.backgroundSelected }]}
            >
              <Text style={{ color: theme.text }}>{delta < 0 ? '前移 0.1 秒' : '后移 0.1 秒'}</Text>
            </Pressable>
          ))}
        </View>
        <Text style={{ color: theme.textSecondary }}>
          在画面中项圈开始明显晃动的位置暂停，再选择对应的 IMU 触发。
        </Text>
        {markers.map((marker) => (
          <Pressable
            key={`${marker.boot}-${marker.seq}`}
            onPress={() => setSelected(marker)}
            style={[
              styles.button,
              { backgroundColor: selected === marker ? theme.tint : theme.backgroundSelected },
            ]}
          >
            <Text style={{ color: theme.text }}>
              {marker.id.slice(-8)} · {bj(marker.utc_us / 1000)} · #{marker.seq}
            </Text>
          </Pressable>
        ))}
        {!markers.length && <Text style={{ color: theme.warning }}>尚未收到同步触发样本</Text>}
        <Pressable
          disabled={!selected || clip.state !== 'uploaded' || saving}
          onPress={confirm}
          style={[styles.button, { backgroundColor: theme.tint }]}
        >
          <Text>{saving ? '保存中' : '确认画面对齐'}</Text>
        </Pressable>
        <Text style={{ color: theme.text }}>{message}</Text>
        <Pressable onPress={close} style={styles.button}>
          <Text style={{ color: theme.text }}>关闭</Text>
        </Pressable>
      </ScrollView>
    </Modal>
  );
}

function ActionButton({
  label,
  onPress,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
}) {
  const theme = useTheme();
  return (
    <Pressable
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.button,
        { backgroundColor: theme.backgroundSelected, opacity: disabled ? 0.4 : 1 },
      ]}
    >
      <Text style={{ color: theme.text }}>{label}</Text>
    </Pressable>
  );
}

export default function CollectionRoute() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Redirect href="/login" />;
  return <CollectionScreen key={user.id} ownerId={user.id} />;
}

function CollectionScreen({ ownerId }: { ownerId: number }) {
  useKeepAwake();
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const navigation = useNavigation();
  const camera = useRef<CameraView>(null);
  const [permission, requestCamera] = useCameraPermissions();
  const [mic, requestMic] = useMicrophonePermissions();
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [clock, setClock] = useState<Clock | null>(null);
  const clockRef = useRef<Clock | null>(null);
  const [session, setSession] = useState('');
  const [device, setDevice] = useState<DeviceFrame | null>(null);
  const [received, setReceived] = useState(0);
  const [clips, setClips] = useState<Clip[]>([]);
  const [review, setReview] = useState<Clip | null>(null);
  const [action, setAction] = useState('');
  const desired = useRef('');
  const current = useRef<Clip | null>(null);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [message, setMessage] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const [countdown, setCountdown] = useState<number | null>(null);
  const countdownTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const debounce = useRef(0);
  const uploading = useRef(false);
  const active = useRef(true);
  const stopReason = useRef(false);
  const stopping = useRef(false);

  useEffect(() => {
    active.current = true;
    Promise.all([loadToken(), fetchMyDevice()])
      .then(([value, result]) => {
        if (!active.current) return;
        setToken(value ?? '');
        setDeviceId(result.device?.deviceId ?? '');
        if (!result.device) setMessage('请先在“我的”页面绑定项圈');
      })
      .catch((e) => setMessage(String(e)));
    if (Platform.OS !== 'web') {
      loadClips(ownerId)
        .then(setClips)
        .catch((e) => setMessage(String(e)));
    }
    const timer = setInterval(
      () => setNow(clockRef.current ? utcNow(clockRef.current) : Date.now()),
      1000,
    );
    const sub = AppState.addEventListener('change', (state) => {
      if (state !== 'active' && current.current) {
        desired.current = '';
        stopReason.current = true;
        camera.current?.stopRecording();
      }
    });
    return () => {
      active.current = false;
      clearInterval(timer);
      sub.remove();
      desired.current = '';
      if (countdownTimer.current) clearInterval(countdownTimer.current);
    };
  }, [ownerId]);
  useEffect(() => {
    if (!ready) return;
    const view = camera.current;
    return () => {
      view?.stopRecording();
    };
  }, [ready]);
  useEffect(
    () =>
      navigation.addListener('beforeRemove', (e) => {
        if (current.current || uploading.current) {
          e.preventDefault();
          Alert.alert('任务进行中', '请先结束录像并等待保存或上传完成。');
        }
      }),
    [navigation],
  );
  useEffect(() => {
    if (!token || !deviceId) return;
    let cancelled = false;
    let running = false;
    const poll = async () => {
      if (running) return;
      running = true;
      try {
        const status = await api<{ received_us: number | null; frame: DeviceFrame | null }>(
          token,
          `/device/${deviceId}`,
        );
        if (!cancelled) {
          setDevice(status.frame);
          setReceived((status.received_us ?? 0) / 1000);
        }
      } catch {
      } finally {
        running = false;
      }
    };
    void poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [token, deviceId]);
  useEffect(() => {
    if (!session || !token) return;
    let cancelled = false;
    const timer = setInterval(() => {
      synchronize(token)
        .then((value) => {
          if (!cancelled) {
            clockRef.current = value;
            setClock(value);
          }
        })
        .catch(() => {});
    }, 60000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [session, token]);
  const reload = useCallback(async () => {
    if (active.current && Platform.OS !== 'web') setClips(await loadClips(ownerId));
  }, [ownerId]);
  async function connect() {
    setBusy(true);
    try {
      const value = await synchronize(token);
      clockRef.current = value;
      setClock(value);
      const id = newId('session');
      await api(token, '/sessions', { id, device: deviceId, clock: value });
      setSession(id);
      setMessage(`北京时间已校准，网络误差约 ±${value.uncertaintyMs.toFixed(0)} ms`);
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  }
  async function collar(start: boolean) {
    setBusy(true);
    try {
      await api(token, `/sessions/${session}/control`, { action: start ? 'start' : 'stop' });
      setMessage(
        start ? '已发送启动请求，等待 SD 写入状态确认' : '已发送停止请求，等待板端状态确认',
      );
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  }
  const stop = useCallback(async () => {
    if (stopping.current) return;
    stopping.current = true;
    desired.current = '';
    setAction('');
    if (countdownTimer.current) clearInterval(countdownTimer.current);
    countdownTimer.current = null;
    setCountdown(null);
    const clip = current.current;
    if (!clip) {
      stopping.current = false;
      return;
    }
    if (clip) {
      const mono = performance.now();
      const label = clip.labels[clip.labels.length - 1];
      label.end_ms = mono - clip.start_mono_ms;
      label.end_utc_ms = utcNow(clip.clock, mono);
    }
    camera.current?.stopRecording();
  }, []);
  const record = useCallback(
    async function recordNext(event: string, marker?: string) {
      const anchor = clockRef.current;
      if (!anchor || !session || !ready || current.current) return;
      const mono = performance.now();
      const utc = utcNow(anchor, mono);
      const clip: Clip = {
        owner_id: ownerId,
        device_id: deviceId,
        calibration_marker: marker,
        id: newId('video'),
        session_id: session,
        event_id: event,
        start_mono_ms: mono,
        start_utc_ms: utc,
        labels: [{ event_id: event, start_ms: 0, start_utc_ms: utc }],
        clock: anchor,
        state: 'recording',
        timestamp_method: 'recordAsync_invocation_not_first_frame_PTS',
      };
      current.current = clip;
      setRecording(true);
      setAction(event);
      stopReason.current = false;
      try {
        await saveClip(clip);
        if (stopping.current || stopReason.current || !active.current)
          throw new Error('录像启动已取消');
        const start = performance.now();
        clip.start_mono_ms = start;
        clip.start_utc_ms = utcNow(anchor, start);
        clip.labels[0].start_utc_ms = clip.start_utc_ms;
        const videoPromise = camera.current!.recordAsync({ maxDuration: 300 });
        await saveClip(clip);
        const video = await videoPromise;
        if (!video?.uri) throw new Error('相机未返回完整视频');
        const end = performance.now();
        clip.duration_ms = end - start;
        clip.end_utc_ms = utcNow(anchor, end);
        const label = clip.labels[clip.labels.length - 1];
        if (label.end_ms === undefined) {
          label.end_ms = end - start;
          label.end_utc_ms = clip.end_utc_ms;
        }
        clip.interrupted = stopReason.current;
        await retainVideo(clip, video.uri);
        setMessage('视频已保存在手机，等待上传');
      } catch (error) {
        clip.error = String(error);
        clip.state = 'interrupted';
        clip.interrupted = true;
        desired.current = '';
        await saveClip(clip).catch(() => {});
        setMessage(String(error));
      } finally {
        current.current = null;
        stopping.current = false;
        setRecording(false);
        await reload();
        const next = desired.current;
        if (next && active.current && !stopReason.current) void recordNext(next);
        else setAction('');
      }
    },
    [session, ready, reload, ownerId, deviceId],
  );
  const choose = useCallback(
    async (event: string) => {
      if (
        stopping.current ||
        !device?.capture_active ||
        device.session_id !== session ||
        !clockRef.current ||
        utcNow(clockRef.current) - received > 15000
      )
        return;
      if (performance.now() - debounce.current < 800) return;
      debounce.current = performance.now();
      if (action === event) {
        await stop();
        return;
      }
      desired.current = event;
      setAction(event);
      const clip = current.current;
      if (clip) {
        const mono = performance.now(),
          utc = utcNow(clip.clock, mono),
          relative = mono - clip.start_mono_ms;
        const last = clip.labels[clip.labels.length - 1];
        last.end_ms = relative;
        last.end_utc_ms = utc;
        clip.labels.push({ event_id: event, start_ms: relative, start_utc_ms: utc });
        await saveClip(clip);
      } else void record(event);
    },
    [action, record, stop, device, session, received],
  );
  async function calibrate() {
    if (
      !session ||
      !device?.capture_active ||
      device.session_id !== session ||
      !clockRef.current ||
      utcNow(clockRef.current) - received > 15000
    ) {
      setMessage('请先等待项圈 SD 采集启动成功');
      return;
    }
    if (recording) {
      setMessage('先结束当前录像，再录制校准片段');
      return;
    }
    try {
      setBusy(true);
      const marker = newId('shake');
      desired.current = 'calibration';
      void record('calibration', marker);
      await api(token, `/sessions/${session}/control`, { action: 'arm', marker });
      setMessage('保持项圈静止，等待板端确认');
      let confirmed = false;
      for (let attempt = 0; attempt < 30; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        if (!active.current || !current.current) throw new Error('校准已取消');
        const status = await api<{ frame: DeviceFrame | null }>(token, `/device/${deviceId}`);
        if (
          status.frame?.session_id === session &&
          status.frame?.armed_marker === marker &&
          status.frame?.marker_ready
        ) {
          confirmed = true;
          break;
        }
      }
      if (!confirmed) throw new Error('项圈未确认静止校准状态，请结束录像后重试');
      setCountdown(5);
      let remaining = 5;
      countdownTimer.current = setInterval(() => {
        remaining--;
        setCountdown(remaining);
        if (remaining === 0) {
          clearInterval(countdownTimer.current!);
          countdownTimer.current = null;
          setMessage('现在晃动项圈一次，完成后结束录像');
        }
      }, 1000);
    } catch (error) {
      setMessage(String(error));
      await stop();
    } finally {
      setBusy(false);
    }
  }
  async function upload(clip: Clip) {
    if (uploading.current || recording) return;
    uploading.current = true;
    setBusy(true);
    try {
      await uploadClip(token, clip, (bytes) =>
        setMessage(`上传 ${Math.round((bytes / (clip.size ?? 1)) * 100)}%`),
      );
      setMessage('视频与动作标注已落盘到云端');
    } catch (error) {
      clip.error = String(error);
      await saveClip(clip);
      setMessage(`${error}，本地文件已保留，可重试续传`);
    } finally {
      uploading.current = false;
      setBusy(false);
      await reload();
    }
  }
  const color = { color: theme.text };
  const muted = { color: theme.textSecondary };
  const fresh = received > 0 && now - received < 15000;
  const canLabel =
    ready &&
    !!session &&
    !busy &&
    fresh &&
    !!device?.capture_active &&
    device.session_id === session;
  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={[
        styles.page,
        { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 24 },
      ]}
    >
      <View style={styles.row}>
        <Pressable
          onPress={() => {
            if (recording) {
              Alert.alert('录像中', '请先停止录像');
              return;
            }
            router.back();
          }}
          accessibilityLabel="返回"
        >
          <SymbolView
            name={{ ios: 'chevron.left', android: 'arrow_back', web: 'arrow_back' }}
            size={24}
            tintColor={theme.text}
          />
        </Pressable>
        <Text style={[styles.title, color]}>PetSense 数据采集</Text>
      </View>
      <Text style={muted}>
        {clock ? bj(now) : '北京时间未校准'} · {fresh ? '项圈已连接' : '等待项圈'}
      </Text>
      {!session && (
        <View style={styles.row}>
          <Text style={muted}>{deviceId || '未绑定项圈'}</Text>
          <ActionButton
            label="连接并校时"
            onPress={connect}
            disabled={busy || !token || !deviceId}
          />
        </View>
      )}
      {!!session && (
        <Text selectable style={muted}>
          {deviceId} · {session}
        </Text>
      )}
      <View style={styles.row}>
        <ActionButton
          label="开始项圈采集"
          onPress={() => collar(true)}
          disabled={
            busy ||
            !session ||
            !!device?.capture_active ||
            !fresh ||
            !device?.utc_us ||
            device.clock_age_us > 300000000
          }
        />
        <ActionButton
          label="停止项圈采集"
          onPress={() => collar(false)}
          disabled={busy || !session || recording}
        />
        <ActionButton
          label="录制晃动校准"
          onPress={calibrate}
          disabled={
            !ready ||
            !session ||
            busy ||
            recording ||
            !fresh ||
            !device?.capture_active ||
            device.session_id !== session
          }
        />
      </View>
      <Text
        style={{
          color: device?.disk_errors || device?.queue_drops ? theme.danger : theme.textSecondary,
        }}
      >
        项圈时钟 {device?.utc_us ? '已校准' : '等待校准'} · SD{' '}
        {device?.sd_ready ? '已挂载' : '未就绪'} · {device?.capture_active ? '正在采集' : '未采集'}{' '}
        · 落盘 {device?.persisted_rows ?? 0} 条{'\n'}
        缺失时隙 {device?.missed_deadlines ?? 0} · 队列丢样 {device?.queue_drops ?? 0} · 写入错误{' '}
        {device?.disk_errors ?? 0}
      </Text>
      {Platform.OS === 'web' ? (
        <View style={[styles.preview, { backgroundColor: theme.backgroundSelected }]}>
          <Text style={color}>录像请在 iPhone 的 Expo Go 中打开</Text>
        </View>
      ) : permission?.granted && mic?.granted ? (
        <CameraView
          ref={camera}
          mode="video"
          facing="back"
          videoQuality="1080p"
          style={styles.preview}
          onCameraReady={() => setReady(true)}
          onMountError={(e) => setMessage(e.message)}
        />
      ) : (
        <ActionButton
          label="允许相机与麦克风"
          onPress={async () => {
            await requestCamera();
            await requestMic();
          }}
        />
      )}
      {countdown !== null && (
        <Text style={[styles.countdown, { color: theme.tint }]}>
          {countdown > 0 ? `保持静止 ${countdown}` : '晃动项圈'}
        </Text>
      )}
      <View style={styles.row}>
        <Text style={[color, { flex: 1 }]}>
          {recording
            ? `录像中 · ${EVENTS.find((v) => v[0] === action)?.[1] ?? '时间校准'}`
            : '待录制'}{' '}
          · {clock ? `时钟误差 ±${clock.uncertaintyMs.toFixed(0)} ms` : ''}
        </Text>
        <ActionButton label="结束录像" onPress={stop} disabled={!recording} />
      </View>
      <View style={styles.events}>
        {EVENTS.map(([id, label]) => (
          <Pressable
            key={id}
            accessibilityRole="button"
            accessibilityState={{ selected: action === id, disabled: !canLabel }}
            disabled={!canLabel}
            onPress={() => choose(id)}
            style={[
              styles.event,
              {
                backgroundColor: action === id ? theme.tint : theme.backgroundSelected,
                opacity: canLabel ? 1 : 0.45,
              },
            ]}
          >
            <Text style={[color, { fontWeight: action === id ? '700' : '400' }]}>{label}</Text>
          </Pressable>
        ))}
      </View>
      <Text accessibilityLiveRegion="polite" style={{ color: theme.warning }}>
        {message}
      </Text>
      <Text style={[color, { fontSize: 20, fontWeight: '700' }]}>本机录像</Text>
      {clips.map((clip) => (
        <View key={clip.id} style={[styles.clip, { borderColor: theme.backgroundSelected }]}>
          <Text style={color}>
            {bj(clip.start_utc_ms)} · {clip.labels.length} 段标签
          </Text>
          <Text style={muted}>
            {clip.state === 'uploaded'
              ? '云端已确认'
              : clip.uri
                ? '待上传 / 可续传'
                : '录像中断，文件尚未确认'}{' '}
            · {((clip.size ?? 0) / 1024 / 1024).toFixed(1)} MB
          </Text>
          <View style={styles.row}>
            <ActionButton
              label={clip.state === 'uploaded' ? '已上传' : '上传 / 重试'}
              onPress={() => upload(clip)}
              disabled={busy || recording || !clip.uri || clip.state === 'uploaded'}
            />
            <ActionButton
              label="查看 / 对齐"
              onPress={() => setReview(clip)}
              disabled={!clip.uri || recording}
            />
          </View>
          {!!clip.error && <Text style={{ color: theme.danger }}>{clip.error}</Text>}
        </View>
      ))}
      {review && <Review clip={review} token={token} close={() => setReview(null)} />}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  page: { paddingHorizontal: 18, gap: 14, maxWidth: 720, width: '100%', alignSelf: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 10 },
  title: { fontSize: 22, fontWeight: '700' },
  button: {
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 8,
    minHeight: 44,
    justifyContent: 'center',
  },
  input: { flexGrow: 1, minWidth: 180, borderWidth: 1, padding: 12, borderRadius: 8 },
  preview: {
    width: '100%',
    aspectRatio: 4 / 3,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
    borderRadius: 8,
  },
  events: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  event: {
    flexBasis: '30%',
    flexGrow: 1,
    minHeight: 48,
    padding: 10,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: 8,
  },
  countdown: { fontSize: 28, fontWeight: '700', textAlign: 'center' },
  clip: { borderTopWidth: 1, paddingVertical: 12, gap: 8 },
});
