export type ObservationPeriod = 'day' | 'week' | 'month';
export type MetricKey = 'heartRate' | 'spo2' | 'bodyTemp' | 'envTemp';
export type MetricSummary = {
  avg: number | null;
  min: number | null;
  max: number | null;
  count: number;
};
export type ObservationBucket = {
  startAt: string;
  label: string;
  observedSeconds: number;
  metrics: Record<MetricKey, MetricSummary>;
};
export type SoundEvent = {
  id: number;
  label: string;
  startedAt: string;
  lastSeenAt: string;
  frames: number;
  confidence: number;
  observedSeconds: number;
  experimental: boolean;
};
/** 今日活动时间轴的连续分钟段：走动 / 静止（含未识别的在线时段）/ 无数据 */
export type TimelineRunState = 'walk' | 'idle' | 'none';
export type TimelineRun = { state: TimelineRunState; minutes: number };
export type DayTimeline = { startAt: string; slotSeconds: number; runs: TimelineRun[] };
export type Observations = {
  deviceId: string | null;
  period: ObservationPeriod;
  latest: { payload: Record<string, unknown>; receivedAt: string } | null;
  summary: {
    observedSeconds: number;
    walkingSeconds: number;
    stationarySeconds: number;
    soundEpisodeCount: number;
    firstObservedAt: string | null;
  };
  events: SoundEvent[];
  metrics: Record<MetricKey, MetricSummary>;
  buckets: ObservationBucket[];
  range: { startAt: string; endAt: string };
  retentionDays: number;
  /** 仅 period=day 返回；旧服务端没有该字段 */
  timeline?: DayTimeline | null;
};
