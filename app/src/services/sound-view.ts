import type { SoundEvent } from '@/types/observations';
import type { TelemetryFrame } from '@/types/telemetry';

const VOCAL_NAMES: Record<string, string> = {
  bark: '吠叫',
  howl: '嚎叫',
  whine: '疑似呜咽',
};

/** A stable visible status; stale/absent readings never imply silence. */
export function soundStatus(frame: TelemetryFrame | null, fresh: boolean, bound: boolean) {
  if (!bound) return { label: '声音未连接', available: false };
  if (!fresh) return { label: '声音未更新', available: false };
  const sound = frame?.edge?.sound;
  if (sound?.label === 'warming_up') return { label: '声音识别准备中', available: false };
  if (sound?.label === 'quiet') return { label: '安静', available: true };
  if (
    !sound ||
    !Number.isFinite(sound.confidence) ||
    sound.confidence < 0.6 ||
    sound.confidence > 1
  )
    return { label: '声音暂未识别', available: false };
  const labels: Record<string, string> = {
    bark: '正在吠叫',
    howl: '正在嚎叫',
    whine: '疑似呜咽',
    other: '其他声音',
  };
  return { label: labels[sound.label] ?? '声音暂未识别', available: !!labels[sound.label] };
}

/** Group nearby detections for reading, without claiming continuous vocal duration or bark counts. */
export function groupSoundEvents(events: SoundEvent[]) {
  const ordered = events
    .filter(
      (event) =>
        VOCAL_NAMES[event.label] &&
        Number.isFinite(Date.parse(event.startedAt)) &&
        Number.isFinite(Date.parse(event.lastSeenAt)) &&
        Date.parse(event.lastSeenAt) >= Date.parse(event.startedAt),
    )
    .sort((a, b) => Date.parse(a.startedAt) - Date.parse(b.startedAt));
  const groups: { id: number; startedAt: string; lastSeenAt: string; labels: string[] }[] = [];
  for (const event of ordered) {
    const previous = groups[groups.length - 1];
    if (previous && Date.parse(event.startedAt) - Date.parse(previous.lastSeenAt) <= 30_000) {
      if (!previous.labels.includes(event.label)) previous.labels.push(event.label);
      if (Date.parse(event.lastSeenAt) > Date.parse(previous.lastSeenAt))
        previous.lastSeenAt = event.lastSeenAt;
    } else {
      groups.push({
        id: event.id,
        startedAt: event.startedAt,
        lastSeenAt: event.lastSeenAt,
        labels: [event.label],
      });
    }
  }
  return groups.reverse().map((group) => ({
    ...group,
    label: ['bark', 'howl', 'whine']
      .filter((label) => group.labels.includes(label))
      .map((label) => VOCAL_NAMES[label])
      .join('、'),
  }));
}
