import * as Crypto from 'expo-crypto';
import * as FS from 'expo-file-system/legacy';
import { API_BASE_URL } from '@/config';

export const COLLECTION_URL = API_BASE_URL;
export const EVENTS = [
  ['01_standing', '站立'],
  ['02_sitting', '坐下'],
  ['03_resting', '趴下 / 休息'],
  ['04_sleeping', '睡觉'],
  ['05_walking', '走路'],
  ['06_running', '跑步'],
  ['07_eating', '吃东西'],
  ['08_rolling', '打滚'],
  ['09_toilet', '上厕所'],
  ['10_jumping', '跳跃'],
] as const;
export type Clock = { utcMs: number; monoMs: number; uncertaintyMs: number; measuredAt: string };
export type Label = {
  event_id: string;
  start_ms: number;
  end_ms?: number;
  start_utc_ms: number;
  end_utc_ms?: number;
};
export type Clip = {
  owner_id: number;
  device_id: string;
  calibration_marker?: string;
  id: string;
  session_id: string;
  event_id: string;
  start_utc_ms: number;
  start_mono_ms: number;
  uri?: string;
  size?: number;
  duration_ms?: number;
  end_utc_ms?: number;
  labels: Label[];
  clock: Clock;
  state: 'recording' | 'pending' | 'uploaded' | 'interrupted';
  interrupted?: boolean;
  timestamp_method: string;
  uploadedBytes?: number;
  error?: string;
};
export type DeviceFrame = {
  boot_id: string;
  seq: number;
  mono_us: number;
  utc_us: number | null;
  clock_uncertainty_us: number;
  clock_age_us: number;
  capture_active: boolean;
  armed_marker?: string;
  marker_ready?: boolean;
  session_id: string;
  sd_ready: boolean;
  missed_deadlines: number;
  queue_drops: number;
  disk_errors: number;
  persisted_rows: number;
};
export type Marker = { boot: string; seq: number; mono_us: number; utc_us: number; id: string };

export async function api<T>(
  token: string,
  path: string,
  body?: unknown,
  method?: string,
  timeoutMs = 20000,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${COLLECTION_URL}/api/collection/v1${path}`, {
      method: method ?? (body === undefined ? 'GET' : 'POST'),
      signal: controller.signal,
      headers: { 'X-Petsense-Session': token, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(value.error ?? value.message ?? `HTTP ${response.status}`);
    return value as T;
  } finally {
    clearTimeout(timer);
  }
}

export async function synchronize(token: string): Promise<Clock> {
  let best: Clock | undefined;
  for (let n = 0; n < 5; n++) {
    const start = performance.now();
    const value = await api<{ server_us: number }>(token, '/time');
    const end = performance.now();
    const sample = {
      utcMs: value.server_us / 1000 + (end - start) / 2,
      monoMs: end,
      uncertaintyMs: (end - start) / 2,
      measuredAt: new Date().toISOString(),
    };
    if (!best || sample.uncertaintyMs < best.uncertaintyMs) best = sample;
  }
  return best!;
}
export function utcNow(clock: Clock, mono = performance.now()) {
  return clock.utcMs + mono - clock.monoMs;
}
export function bj(ms: number) {
  return new Date(ms).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}
export function newId(prefix: string) {
  return `${prefix}-${Crypto.randomUUID()}`;
}
const folder = (owner: number) => `${FS.documentDirectory}petsense-collection/${owner}/`;

const writes = new Map<string, Promise<void>>();
export async function saveClip(clip: Clip) {
  const snapshot = JSON.stringify(clip);
  const previous = writes.get(clip.id) ?? Promise.resolve();
  const next = previous.catch(() => {}).then(() => writeClip(clip.owner_id, clip.id, snapshot));
  writes.set(clip.id, next);
  try {
    await next;
  } finally {
    if (writes.get(clip.id) === next) writes.delete(clip.id);
  }
}
async function writeClip(owner: number, id: string, snapshot: string) {
  await FS.makeDirectoryAsync(folder(owner), { intermediates: true });
  // Keep a last-known-good backup when rewriting the annotation on device.
  const path = `${folder(owner)}${id}.json`;
  const temporary = path + '.pending';
  await FS.writeAsStringAsync(temporary, snapshot);
  if ((await FS.getInfoAsync(path)).exists)
    await FS.copyAsync({ from: path, to: path + '.backup' });
  await FS.moveAsync({ from: temporary, to: path });
}
export async function loadClips(owner: number): Promise<Clip[]> {
  await FS.makeDirectoryAsync(folder(owner), { intermediates: true });
  const names = await FS.readDirectoryAsync(folder(owner));
  const ids = new Set(
    names.filter((n) => /\.json(?:\.pending|\.backup)?$/.test(n)).map((n) => n.split('.json')[0]),
  );
  const clips: Clip[] = [];
  for (const id of ids) {
    for (const suffix of ['.json.pending', '.json', '.json.backup']) {
      try {
        const clip = JSON.parse(await FS.readAsStringAsync(folder(owner) + id + suffix)) as Clip;
        if (clip.owner_id === owner) clips.push(clip);
        break;
      } catch {}
    }
  }
  return clips.sort((a, b) => b.start_utc_ms - a.start_utc_ms);
}
export async function retainVideo(clip: Clip, temporary: string) {
  const uri = `${folder(clip.owner_id)}${clip.id}.mov`;
  await FS.copyAsync({ from: temporary, to: uri });
  const info = await FS.getInfoAsync(uri);
  if (!info.exists || !info.size) throw new Error('本地视频保存失败');
  clip.uri = uri;
  clip.size = info.size;
  clip.state = 'pending';
  await saveClip(clip);
}
export async function uploadClip(token: string, clip: Clip, progress: (bytes: number) => void) {
  if (!clip.uri || !clip.size) throw new Error('录像未完整保存，不能标记为上传成功');
  await api(token, `/sessions/${clip.session_id}/videos`, {
    id: clip.id,
    event_id: clip.event_id,
    start_utc_ms: clip.start_utc_ms,
    clock: clip.clock,
    labels: clip.labels,
    calibration_marker: clip.calibration_marker,
  });
  const path = `/sessions/${clip.session_id}/videos/${clip.id}`;
  const status = await api<{ offset: number; complete: boolean }>(token, `${path}/upload`);
  let offset = status.offset;
  if (offset > clip.size) throw new Error('云端视频大小与本地不符');
  while (!status.complete && offset < clip.size) {
    const length = Math.min(512 * 1024, clip.size - offset);
    const encoded = await FS.readAsStringAsync(clip.uri, {
      encoding: FS.EncodingType.Base64,
      position: offset,
      length,
    });
    const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
    const digest = await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, bytes);
    const sha = Array.from(new Uint8Array(digest))
      .map((v) => v.toString(16).padStart(2, '0'))
      .join('');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(
        `${COLLECTION_URL}/api/collection/v1${path}/upload?offset=${offset}`,
        {
          method: 'PUT',
          signal: controller.signal,
          headers: {
            'X-Petsense-Session': token,
            'Content-Type': 'application/octet-stream',
            'X-Chunk-SHA256': sha,
          },
          body: bytes,
        },
      );
      const result = await response.json();
      if (!response.ok) throw new Error(result.error ?? `上传失败 ${response.status}`);
      if (result.offset !== offset + bytes.length) throw new Error('分片确认位置错误');
      offset = result.offset;
      clip.uploadedBytes = offset;
      progress(offset);
      await saveClip(clip);
    } finally {
      clearTimeout(timer);
    }
  }
  await api(
    token,
    `${path}/finish`,
    {
      size: clip.size,
      duration_ms: clip.duration_ms,
      end_utc_ms: clip.end_utc_ms,
      labels: clip.labels,
      clock: clip.clock,
      interrupted: clip.interrupted,
      timestamp_method: clip.timestamp_method,
    },
    'POST',
    360000,
  );
  clip.state = 'uploaded';
  clip.error = undefined;
  await saveClip(clip);
}
