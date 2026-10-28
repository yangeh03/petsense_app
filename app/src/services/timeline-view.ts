import type { DayTimeline, TimelineRunState } from '@/types/observations';

export type TimelineSegment = { key: string; state: TimelineRunState; minutes: number };

const STATES: readonly TimelineRunState[] = ['walk', 'idle', 'none'];

/** 服务端 runs 已覆盖今日 0 点到现在；这里只做防御性过滤，供时间轴按分钟权重绘制。 */
export function timelineSegments(timeline: DayTimeline | null | undefined): TimelineSegment[] {
  const runs = timeline?.runs;
  if (!Array.isArray(runs)) return [];
  return runs
    .filter((run) => run && run.minutes > 0 && STATES.includes(run.state))
    .map((run, index) => ({
      key: `${index}-${run.state}`,
      state: run.state,
      minutes: run.minutes,
    }));
}

/** 走动占已观测时长的整数百分比；没有观测或没有走动时返回 null。 */
export function walkingShare(
  summary: { walkingSeconds: number; observedSeconds: number } | null | undefined,
): number | null {
  if (!summary || !(summary.observedSeconds > 0) || !(summary.walkingSeconds > 0)) return null;
  return Math.round((summary.walkingSeconds / summary.observedSeconds) * 100);
}
