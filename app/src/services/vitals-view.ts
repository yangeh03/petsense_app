import type { MetricKey, MetricSummary, ObservationBucket } from '@/types/observations';
import type { SeriesPoint, TelemetryHealth, TelemetrySeries } from '@/types/telemetry';

export type VitalKey = 'heartRate' | 'spo2' | 'bodyTemp';

export const VITALS: readonly { key: VitalKey; label: string; unit: string; decimals: number }[] = [
  { key: 'heartRate', label: '心率', unit: 'bpm', decimals: 0 },
  { key: 'spo2', label: '血氧', unit: '%', decimals: 0 },
  { key: 'bodyTemp', label: '传感器温度', unit: '℃', decimals: 1 },
] as const;

export type VitalReading = {
  key: VitalKey;
  label: string;
  unit: string;
  decimals: number;
  /** 新鲜且有效的实时值优先，否则回退当日均值并标注来源 */
  value: number | null;
  source: 'live' | 'avg';
  today: { min: number; max: number } | null;
  points: SeriesPoint[];
};

const SPARK_POINTS = 30;

function finitePositive(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

function sparkFromBuckets(buckets: ObservationBucket[], key: VitalKey): SeriesPoint[] {
  const points: SeriesPoint[] = [];
  for (const bucket of buckets) {
    const avg = bucket.metrics?.[key]?.avg;
    if (finitePositive(avg)) points.push({ t: points.length, v: avg });
  }
  return points;
}

/**
 * 三项核心体征的展示值：实时帧有效且新鲜用实时值；
 * 否则回退服务端当日聚合（均值 + 今日区间），绝不把缺失显示成 0。
 */
export function selectVitals({
  health,
  current,
  daily,
  series,
  buckets,
}: {
  health: TelemetryHealth | null;
  current: boolean;
  daily: Partial<Record<MetricKey, MetricSummary>> | null;
  series: TelemetrySeries;
  buckets: ObservationBucket[];
}): VitalReading[] {
  return VITALS.map(({ key, label, unit, decimals }) => {
    const live = current && health?.valid ? health[key] : undefined;
    const isLive = finitePositive(live);
    const summary = daily?.[key];
    const hasDaily = !!summary && summary.count > 0 && summary.avg !== null;
    const livePoints = current ? (series[key] ?? []).slice(-SPARK_POINTS) : [];
    const dailyPoints = sparkFromBuckets(buckets ?? [], key);
    return {
      key,
      label,
      unit,
      decimals,
      value: isLive ? live : hasDaily ? (summary!.avg as number) : null,
      source: isLive ? 'live' : 'avg',
      today:
        hasDaily && summary!.min !== null && summary!.max !== null
          ? { min: summary!.min!, max: summary!.max! }
          : null,
      points: livePoints.length >= 2 ? livePoints : dailyPoints.length >= 2 ? dailyPoints : [],
    };
  });
}

/** 环境温度是情境读数而非体征：有限的非 0 实时值即可用，否则回退当日均值。 */
export function selectEnvTemp({
  health,
  current,
  daily,
}: {
  health: TelemetryHealth | null;
  current: boolean;
  daily: Partial<Record<MetricKey, MetricSummary>> | null;
}): { value: number; source: 'live' | 'avg' } | null {
  const live = current && health?.valid ? health.envTemp : undefined;
  if (typeof live === 'number' && Number.isFinite(live) && live !== 0) {
    return { value: live, source: 'live' };
  }
  const summary = daily?.envTemp;
  if (summary && summary.count > 0 && summary.avg !== null) {
    return { value: summary.avg, source: 'avg' };
  }
  return null;
}

export function formatVital(value: number | null, decimals: number): string {
  return value === null ? '—' : value.toFixed(decimals);
}
