const assert = require('node:assert/strict');
const { test } = require('node:test');
const {
  chartScale,
  chartSegments,
  nearestBucket,
  bucketPosition,
} = require('../src/services/trend-presentation.ts');
const { TrendQuery } = require('../src/services/trend-query.ts');
const hour = 3600000;
const bucket = (start, avg) => ({
  start: new Date(start).toISOString(),
  end: new Date(start + hour).toISOString(),
  metrics: { heartRate: { count: avg == null ? 0 : 1, avg, min: avg, max: avg } },
});

test('missing hours break lines; positions retain real elapsed time', () => {
  const buckets = [bucket(0, 60), bucket(hour, null), bucket(2 * hour, 90), bucket(3 * hour, 100)];
  const segments = chartSegments(buckets, 'heartRate', hour);
  assert.deepEqual(
    segments.map((s) => s.length),
    [1, 2],
  );
  assert.equal(bucketPosition(buckets[2], 0, 4 * hour), 0.625);
});
test('sparse daily archive gaps never connect across absent days', () => {
  const buckets = [bucket(0, 60), bucket(3 * 24 * hour, 90)];
  assert.equal(chartSegments(buckets, 'heartRate', 24 * hour).length, 2);
});
test('constant heart rate has unique integer ticks and includes the value', () => {
  const scale = chartScale([64], 0);
  assert.equal(new Set(scale.ticks).size, scale.ticks.length);
  assert.ok(scale.min <= 64 && scale.max >= 64);
  assert.ok(scale.ticks.every(Number.isInteger));
});
test('temperature ticks remain distinct at display precision', () => {
  const scale = chartScale([38.21, 38.23], 1);
  const labels = scale.ticks.map((v) => v.toFixed(1));
  assert.equal(new Set(labels).size, labels.length);
});
test('selection can choose empty periods and one available point', () => {
  const buckets = [bucket(0, 60), bucket(hour, null), bucket(2 * hour, 90)];
  assert.equal(nearestBucket(buckets, 1.5 * hour), 1);
  assert.equal(nearestBucket([buckets[0]], hour), 0);
  assert.equal(nearestBucket([], hour), -1);
});
test('a late response from another identity cannot replace current data', async () => {
  const query = new TrendQuery();
  let oldResolve;
  const old = query.refresh('user1/day', () => new Promise((r) => (oldResolve = r)));
  await query.refresh('user2/week', async () => ({ marker: 'current' }));
  oldResolve({ marker: 'old' });
  await old;
  assert.equal(query.getSnapshot().data.marker, 'current');
});
test('failed refresh preserves only same-query data and exposes an error', async () => {
  const query = new TrendQuery();
  await query.refresh('a', async () => ({ marker: 'saved' }));
  await query.refresh('a', async () => {
    throw new Error('offline');
  });
  assert.equal(query.getSnapshot().data.marker, 'saved');
  assert.equal(query.getSnapshot().error, 'offline');
  await query.refresh('b', async () => {
    throw new Error('offline');
  });
  assert.equal(query.getSnapshot().data, null);
});
test('invalidation blocks an in-flight result after logout or screen cleanup', async () => {
  const query = new TrendQuery();
  let resolve;
  const pending = query.refresh('a', () => new Promise((r) => (resolve = r)));
  query.reset('logged-out');
  resolve({ marker: 'private' });
  await pending;
  assert.equal(query.getSnapshot().data, null);
});

test('web click without RN locationX uses offsetX; keyboard activation selects latest', () => {
  const { chartTouchTime } = require('../src/services/trend-presentation.ts');
  assert.equal(chartTouchTime(undefined, 90, 40, 100, 0, 1000), 500);
  assert.equal(chartTouchTime(undefined, undefined, 40, 100, 0, 1000), 1000);
  assert.equal(chartTouchTime(-10, undefined, 40, 100, 0, 1000), 0);
});
