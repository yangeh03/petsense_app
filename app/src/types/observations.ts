export type ObservationPeriod = 'day' | 'week' | 'month';
export type MetricKey = 'heartRate' | 'spo2' | 'bodyTemp';
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
export type Observations = {
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
};
