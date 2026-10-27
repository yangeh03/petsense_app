const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const trackingPath = path.join(__dirname, '../src/app/(tabs)/tracking.tsx');

test('location tab preserves real ranging status and labels unfinished positioning', () => {
  const source = fs.readFileSync(trackingPath, 'utf8');
  assert.match(source, /室内\s*\/\s*室外定位/);
  assert.match(source, /筹备中/);
  assert.match(source, /useTelemetry/);
  assert.match(source, /useObservations/);
  assert.match(source, /router\.push\('\/monitor'\)/);
});
