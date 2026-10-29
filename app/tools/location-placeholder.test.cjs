const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const trackingPath = path.join(__dirname, '../src/app/(tabs)/tracking.tsx');

test('location tab shows indoor and GPS together and preserves real ranging status', () => {
  const source = fs.readFileSync(trackingPath, 'utf8');
  assert.match(source, /室内 UWB 定位/);
  assert.match(source, /GPS 定位/);
  assert.match(source, /<GpsMap/);
  assert.match(source, /暂不自动切换/);
  assert.doesNotMatch(source, /室外定位筹备中/);
  assert.match(source, /useTelemetry/);
  assert.match(source, /useObservations/);
  assert.match(source, /router\.push\('\/monitor'\)/);
});
