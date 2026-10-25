import type { TrendBucket, TrendMetric } from '../types/social';

export function chartScale(values: number[], decimals: number) {
  const finite = values.filter(Number.isFinite);
  const low = finite.length ? Math.min(...finite) : 0;
  const high = finite.length ? Math.max(...finite) : 1;
  const precision = 10 ** -decimals;
  const rawStep = Math.max(high - low, 4 * precision) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step = Math.max(
    precision,
    [1, 2, 5, 10].find((n) => n * magnitude >= rawStep)! * magnitude,
  );
  const min = Math.max(0, Math.floor((low - precision) / step) * step);
  const max = Math.ceil((high + precision) / step) * step;
  const ticks = Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) =>
    Number((min + i * step).toFixed(decimals)),
  );
  return { min, max, ticks };
}
export function bucketPosition(bucket: TrendBucket, start: number, end: number) {
  const center = (Date.parse(bucket.start) + Date.parse(bucket.end)) / 2;
  return Math.max(0, Math.min(1, (center - start) / Math.max(1, end - start)));
}
export function chartSegments(buckets: TrendBucket[], metric: TrendMetric, interval: number) {
  const segments: TrendBucket[][] = [];
  let current: TrendBucket[] = [];
  let previous = -Infinity;
  for (const bucket of buckets) {
    const time = Date.parse(bucket.start);
    const value = bucket.metrics[metric].avg;
    if (value == null || !Number.isFinite(value) || time - previous > interval * 1.01) current = [];
    if (value != null && Number.isFinite(value)) {
      if (!current.length) segments.push(current);
      current.push(bucket);
    }
    previous = time;
  }
  return segments;
}
export function nearestBucket(buckets: TrendBucket[], time: number) {
  let nearest = -1;
  let distance = Infinity;
  buckets.forEach((bucket, index) => {
    const start = Date.parse(bucket.start),
      end = Date.parse(bucket.end);
    const delta = time < start ? start - time : time > end ? time - end : 0;
    if (delta < distance) {
      nearest = index;
      distance = delta;
    }
  });
  return nearest;
}
const dateFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: '2-digit',
  day: '2-digit',
});
const timeFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});
export function trendDate(time: string | number, withTime = false) {
  const date = new Date(time);
  return dateFormat.format(date) + (withTime ? ` ${timeFormat.format(date)}` : '');
}

/** RN touch events use locationX; browser click events use offsetX; keyboard has neither. */
export function chartTouchTime(
  locationX: number | undefined,
  offsetX: number | undefined,
  left: number,
  width: number,
  start: number,
  end: number,
) {
  const position = Number.isFinite(locationX) ? locationX : offsetX;
  if (position == null || !Number.isFinite(position)) return end;
  return start + Math.max(0, Math.min(1, (position - left) / Math.max(1, width))) * (end - start);
}
