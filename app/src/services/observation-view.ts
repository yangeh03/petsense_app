import type { DashboardData } from '@/types/social';
import type { Observations } from '@/types/observations';
import type { TelemetryFrame } from '@/types/telemetry';

export const ACTION_NAMES: Record<string, string> = {
  standing: '站立',
  sitting: '坐着',
  'lying down': '趴着',
  walking: '走动',
  unknown: '暂未识别',
  warming_up: '识别准备中',
};
export const SOUND_NAMES: Record<string, string> = {
  bark: '吠叫',
  howl: '嚎叫',
  whine: '呜咽',
  other: '其他声音',
  quiet: '安静',
  unknown: '暂未识别',
  warming_up: '识别准备中',
};

export function currentPrediction(frame: TelemetryFrame | null, fresh: boolean) {
  if (!fresh) return { action: '等待更新', sound: '等待更新', experimental: false };
  const edge = frame?.edge;
  const action = edge?.action;
  const sound = edge?.sound;
  return {
    action:
      action && (action.confidence >= 0.6 || action.label === 'warming_up')
        ? (ACTION_NAMES[action.label] ?? '暂未识别')
        : '暂未识别',
    sound:
      sound && (sound.label === 'quiet' || sound.confidence >= 0.6 || sound.label === 'warming_up')
        ? (SOUND_NAMES[sound.label] ?? '暂未识别')
        : '暂未识别',
    experimental: sound?.experimental === true,
  };
}

export function chartSegments(values: (number | null)[]) {
  const segments: { index: number; value: number }[][] = [];
  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) return;
    if (!index || values[index - 1] === null || !Number.isFinite(values[index - 1]))
      segments.push([]);
    segments[segments.length - 1].push({ index, value });
  });
  return segments;
}

export function duration(seconds: number) {
  if (seconds === 0) return '0秒';
  if (seconds < 1) return '不足1秒';
  if (seconds < 60) return `${Math.floor(seconds)}秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`;
  return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分钟`;
}

export function clockTime(value: string) {
  return new Date(value).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  });
}

/** A partial refresh must never attach a previous collar's history to this pet. */
export function observationsForPet(dashboard: DashboardData | null, data: Observations | null) {
  const device = dashboard?.pet?.device_id;
  return device && data?.deviceId === device ? data : null;
}

/** Keep the visible scale at least as wide as the sensor's useful resolution. */
export function trendScale(values: number[], metric: string) {
  const low = values.length ? Math.min(...values) : 0;
  const high = values.length ? Math.max(...values) : 1;
  const padding = Math.max((high - low) * 0.15, metric === 'bodyTemp' ? 0.1 : 1);
  const min = metric === 'spo2' ? Math.max(0, low - padding) : low - padding;
  const max = metric === 'spo2' ? Math.min(100, high + padding) : high + padding;
  const step = (max - min) / 2;
  const decimals = Math.max(
    metric === 'bodyTemp' ? 1 : 0,
    step > 0 ? -Math.floor(Math.log10(step)) : 0,
  );
  return { min, max, decimals, ticks: [max, (min + max) / 2, min] };
}
