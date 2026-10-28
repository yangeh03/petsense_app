const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
function load(file) {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(require('node:path').join(__dirname, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    { module, exports: module.exports, require: () => undefined },
  );
  return module.exports;
}
const { selectVitals, selectEnvTemp, formatVital } = load('../src/services/vitals-view.ts');
const { timelineSegments, walkingShare } = load('../src/services/timeline-view.ts');

/** Objects created inside the transpile VM have a foreign prototype; normalize before comparing. */
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

const daily = {
  heartRate: { avg: 60.89, min: 52, max: 111, count: 1070 },
  spo2: { avg: 95.49, min: 74, max: 99, count: 972 },
  bodyTemp: { avg: 40.53, min: 35, max: 45.9, count: 1071 },
  envTemp: { avg: 31.2, min: 30, max: 32.4, count: 900 },
};
const series = {
  heartRate: [
    { t: 1, v: 62 },
    { t: 2, v: 64 },
  ],
  spo2: [],
  bodyTemp: [],
};
const buckets = [
  { startAt: '', label: '10:00', observedSeconds: 600, metrics: daily },
  { startAt: '', label: '11:00', observedSeconds: 600, metrics: daily },
];

function validHealth(overrides = {}) {
  return { valid: true, heartRate: 0, spo2: 0, bodyTemp: 0, envTemp: 0, ...overrides };
}

test('fresh valid health wins over day averages and uses the live series', () => {
  const vitals = selectVitals({
    health: validHealth({ heartRate: 63, spo2: 97, bodyTemp: 40.3, envTemp: 31.2 }),
    current: true,
    daily,
    series,
    buckets: [],
  });
  assert.deepEqual(plain(vitals.map((v) => [v.value, v.source])), [
    [63, 'live'],
    [97, 'live'],
    [40.3, 'live'],
  ]);
  assert.equal(vitals[0].points.length, 2);
  assert.deepEqual(plain(vitals[0].today), { min: 52, max: 111 });
});

test('online but invalid zeros fall back to labelled day averages', () => {
  const vitals = selectVitals({
    health: validHealth(),
    current: true,
    daily,
    series,
    buckets,
  });
  assert.deepEqual(plain(vitals.map((v) => [v.value, v.source])), [
    [60.89, 'avg'],
    [95.49, 'avg'],
    [40.53, 'avg'],
  ]);
  assert.ok(vitals[0].points.length >= 2, 'daily buckets still draw a trend line');
});

test('offline with no history renders nothing invented', () => {
  const vitals = selectVitals({
    health: null,
    current: false,
    daily: null,
    series: { heartRate: [], spo2: [], bodyTemp: [] },
    buckets: [],
  });
  assert.ok(vitals.every((v) => v.value === null && v.today === null && v.points.length === 0));
  assert.equal(formatVital(null, 0), '—');
  assert.equal(formatVital(40.25, 1), '40.3');
});

test('env temperature prefers a non-zero live reading, else the day average', () => {
  assert.deepEqual(
    plain(selectEnvTemp({ health: validHealth({ envTemp: 31.2 }), current: true, daily })),
    { value: 31.2, source: 'live' },
  );
  assert.deepEqual(
    plain(selectEnvTemp({ health: validHealth({ envTemp: 0 }), current: true, daily })),
    { value: 31.2, source: 'avg' },
  );
  assert.equal(selectEnvTemp({ health: null, current: false, daily: null }), null);
});

test('timeline segments pass through runs and drop empty or unknown entries', () => {
  const segments = timelineSegments({
    startAt: '',
    slotSeconds: 60,
    runs: [
      { state: 'none', minutes: 868 },
      { state: 'walk', minutes: 0 },
      { state: 'walk', minutes: 1 },
      { state: 'nonsense', minutes: 5 },
      { state: 'idle', minutes: 2 },
    ],
  });
  assert.deepEqual(plain(segments), [
    { key: '0-none', state: 'none', minutes: 868 },
    { key: '1-walk', state: 'walk', minutes: 1 },
    { key: '2-idle', state: 'idle', minutes: 2 },
  ]);
  assert.deepEqual(plain(timelineSegments(null)), []);
  assert.deepEqual(plain(timelineSegments(undefined)), []);
});

test('walking share is honest about nothing observed or nothing walked', () => {
  assert.equal(walkingShare(null), null);
  assert.equal(walkingShare({ observedSeconds: 0, walkingSeconds: 0 }), null);
  assert.equal(walkingShare({ observedSeconds: 5598, walkingSeconds: 0 }), null);
  assert.equal(walkingShare({ observedSeconds: 5598, walkingSeconds: 132 }), 2);
});
