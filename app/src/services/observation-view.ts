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
    if (!index || values[index - 1] === null) segments.push([]);
    segments[segments.length - 1].push({ index, value });
  });
  return segments;
}

export function duration(seconds: number) {
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
