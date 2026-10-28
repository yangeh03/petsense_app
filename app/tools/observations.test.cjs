const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
function load(file, dependencies = {}) {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(require('node:path').join(__dirname, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    {
      module,
      exports: module.exports,
      require: (name) => dependencies[name],
      setTimeout,
      clearTimeout,
      setInterval: () => 0,
      clearInterval: () => {},
    },
  );
  return module.exports;
}
const { parseTelemetryMessage } = load('../src/services/telemetry-service.ts', {
  '@/config': { TELEMETRY_MODE: 'live' },
  '@/data/replay-dataset': {},
  '@/services/api': {},
});
const raw = {
  deviceId: 'edge',
  ts_ms: 123,
  source: 'esp32_mlp',
  modelVersion: 'v3',
  action: { label: 'lying down', confidence: 1 },
  sound: { label: 'quiet', confidence: 0, ac_rms: 0.0003 },
  battery: { found: true, percent: 0, voltage: 3.8 },
};
const parse = (payload) =>
  parseTelemetryMessage(
    JSON.stringify({
      topic: 'petsense/sensor/edge_v1',
      received_at: '2026-10-25T06:00:00Z',
      payload,
    }),
  );

test('SD telemetry preserves zero free bytes but rejects missing and malformed capacity', () => {
  const frame = parse({
    ...raw,
    sd_free_bytes: 0,
    sd_total_bytes: 1048576,
    sd_used_bytes: 1048576,
  });
  assert.equal(frame.edge.sdFreeBytes, 0);
  assert.equal(frame.edge.sdTotalBytes, 1048576);
  for (const value of [null, -1, '1024']) {
    assert.equal(parse({ ...raw, sd_free_bytes: value }).edge.sdFreeBytes, undefined);
  }
  const { storageSpace } = load('../src/services/storage-space.ts');
  assert.equal(storageSpace(0), '0.0 MiB');
  assert.equal(storageSpace(null), '未上报');
  assert.equal(storageSpace(1048576), '1.0 MiB');
});
test('real edge schema preserves resting prediction and quiet zero confidence', () => {
  const frame = parse(raw);
  assert.equal(frame.edge.action.label, 'lying down');
  assert.equal(frame.edge.sound.label, 'quiet');
  assert.equal(frame.edge.sound.confidence, 0);
  assert.equal(frame.battery.percent, 0);
});
test('unknown labels and malformed confidence never become confident predictions', () => {
  const frame = parse({
    ...raw,
    action: { label: 'sleeping', confidence: 0.9 },
    sound: { label: 'bark', confidence: 5 },
  });
  assert.equal(frame.edge.action, undefined);
  assert.equal(frame.edge.sound, undefined);
});
test('experimental whine and model provenance survive parsing', () => {
  const frame = parse({ ...raw, sound: { label: 'whine', confidence: 0.8, experimental: true } });
  assert.equal(frame.edge.sound.experimental, true);
  assert.equal(frame.edge.modelVersion, 'v3');
});
const view = () => load('../src/services/observation-view.ts');
test('stale or unknown predictions never imply standing or quiet', () => {
  const { currentPrediction } = view();
  assert.equal(currentPrediction(parse(raw), false).action, '等待更新');
  assert.equal(
    currentPrediction(parse({ ...raw, action: { label: 'unknown', confidence: 0.7 } }), true)
      .action,
    '暂未识别',
  );
  assert.equal(currentPrediction(parse(raw), true).sound, '安静');
});
test('missing time buckets split chart segments instead of bridging gaps', () => {
  const { chartSegments } = view();
  const segments = chartSegments([60, null, 90, 100, null, 80]);
  assert.equal(segments.length, 3);
  assert.equal(segments[1][0].index, 2);
  assert.equal(segments[2][0].index, 5);
});
test('real experiment replay continues to emit health without requiring an edge payload', () => {
  const { telemetryService } = load('../src/services/telemetry-service.ts', {
    '@/config': { TELEMETRY_MODE: 'replay' },
    '@/services/api': {},
    '@/data/replay-dataset': {
      REPLAY_META: { deviceId: 'real-dataset', cadenceMs: 1000 },
      REPLAY_HEALTH: [[0, 1, 120, 98, 38, 24, 20, 0, 0, 0, 0, 0, 0, 0]],
      REPLAY_BATTERY: [],
    },
  });
  let frame;
  const off = telemetryService.subscribeFrame((value) => {
    frame = value;
  });
  assert.equal(frame.health.heartRate, 120);
  assert.equal(frame.edge, undefined);
  off();
});
test('cached history is discarded when a successful dashboard refresh unbinds or replaces its device', () => {
  const { observationsForPet } = view();
  const data = { deviceId: 'old-collar', summary: { soundEpisodeCount: 12 } };
  assert.equal(observationsForPet({ pet: { device_id: '' } }, data), null);
  assert.equal(observationsForPet({ pet: { device_id: 'new-collar' } }, data), null);
  assert.equal(observationsForPet(null, data), null);
  assert.equal(observationsForPet({ pet: { device_id: 'old-collar' } }, data), data);
});
test('nonfinite chart values also create a gap', () => {
  const { chartSegments } = view();
  const result = chartSegments([NaN, 60, Infinity, 90]);
  assert.equal(result.length, 2);
  assert.equal(result[0][0].index, 1);
});
test('minute changes do not turn a hundredth of a heartbeat into a full-height spike', () => {
  const { trendScale } = view();
  const scale = trendScale([64, 64.01], 'heartRate');
  assert.ok(scale.max - scale.min >= 2);
  assert.equal(scale.ticks.map((value) => value.toFixed(scale.decimals)).join(','), '65,64,63');
});
