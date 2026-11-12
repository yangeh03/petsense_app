const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

const exportsObject = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(path.join(__dirname, '../src/services/telemetry-service.ts'), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
  ).outputText,
  {
    exports: exportsObject,
    require: (name) => {
      if (name === '@/config' || name === '@/data/replay-dataset') return {};
      if (name === '@/services/api') return { getToken: () => null };
      throw new Error(name);
    },
    setTimeout,
    clearTimeout,
    WebSocket,
  },
);
const fix = {
  source: 'A7670E_GNSS',
  crs: 'WGS84',
  enabled: true,
  valid: true,
  status: 'fixed',
  latitude: 0,
  longitude: 0,
  utc: '2026-10-29T08:00:00.000Z',
  remainingMs: 13000,
};
function parse(gps) {
  return exportsObject.parseTelemetryMessage(
    JSON.stringify({
      topic: 'petsense/sensor/edge_v1',
      received_at: '2026-10-29T08:00:02.000Z',
      payload: { deviceId: 'collar', ts_ms: 12000, gps },
    }),
  );
}
test('GNSS-only telemetry keeps real zero coordinates', () => {
  const gps = parse(fix).gps;
  assert.equal(gps.valid, true);
  assert.equal(gps.latitude, 0);
  assert.equal(gps.longitude, 0);
});
test('unfixed, stale and malformed GNSS never produce coordinates', () => {
  for (const change of [
    { valid: false, status: 'searching' },
    { remainingMs: 0 },
    { latitude: NaN },
    { longitude: 181 },
    { enabled: false },
    { utc: 'invalid' },
  ]) {
    const gps = parse({ ...fix, ...change }).gps;
    assert.equal(gps.valid, false);
    assert.equal(gps.latitude, null);
    assert.equal(gps.longitude, null);
  }
  assert.equal(parse({ ...fix, source: 'unknown' }), null);
});
