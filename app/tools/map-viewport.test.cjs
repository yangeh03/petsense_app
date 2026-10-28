const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

const exportsObject = {};
const code = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../src/services/map-viewport.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } },
).outputText;
vm.runInNewContext(code, { exports: exportsObject });
const { clampMapPan, mapBounds, appendMeasuredTrail, positionMatchesMap, backgroundImageRect } =
  exportsObject;
const telemetryExports = {};
const telemetrySource = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../src/services/telemetry-service.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
vm.runInNewContext(telemetrySource, {
  exports: telemetryExports,
  require: (name) => {
    if (name === '@/config' || name === '@/data/replay-dataset') return {};
    if (name === '@/services/api') return { getToken: () => null };
    throw new Error(name);
  },
  setTimeout,
  clearTimeout,
  WebSocket,
});

test('telemetry position parser rejects another device and malformed coordinates', () => {
  const p = {
    schemaVersion: 1,
    deviceId: 'collar',
    mapId: 'room',
    mapVersion: 'v1',
    status: 'ok',
    fresh: true,
    remainingMs: 1000,
    position: { x: 1, y: 2 },
    quality: {},
  };
  assert.equal(telemetryExports.parseUwbPosition(p, 'collar').position.x, 1);
  assert.equal(telemetryExports.parseUwbPosition(p, 'other'), undefined);
  assert.equal(
    telemetryExports.parseUwbPosition({ ...p, position: { x: NaN, y: 2 } }, 'collar'),
    undefined,
  );
  assert.equal(
    telemetryExports.parseUwbPosition({ ...p, remainingMs: Infinity }, 'collar'),
    undefined,
  );
});
const map = {
  mapId: 'room',
  version: 'v1',
  background: { origin: [-2, -1], width: 400, height: 200, metersPerPixel: 0.01 },
  polygon: [
    [-2, -1],
    [2, -1],
    [2, 1],
    [-2, 1],
  ],
  anchors: [],
};

test('map bounds preserve metre coordinates and non-square image aspect', () => {
  const b = mapBounds(map);
  assert.equal(b.minX, -2);
  assert.equal(b.minY, -1);
  assert.equal(b.width, 4);
  assert.equal(b.height, 2);
});

test('pan is clamped after zoom/resize and cannot change physical positions', () => {
  const p = { x: 1000, y: -1000 };
  const b = mapBounds(map);
  const fitted = clampMapPan(p, { width: 400, height: 200 }, b, 1);
  assert.equal(fitted.x, 0);
  assert.equal(fitted.y, 0);
  const zoomed = clampMapPan(p, { width: 400, height: 200 }, b, 2);
  assert.equal(zoomed.x, 200);
  assert.equal(zoomed.y, -100);
  assert.equal(p.x, 1000);
  assert.equal(map.polygon[0][0], -2);
});

test('background image rect replicates the svg meet fit for native rendering', () => {
  const b = mapBounds(map);
  const exact = backgroundImageRect(map, b, { width: 400, height: 200 });
  assert.deepEqual({ ...exact }, { left: 0, top: 0, width: 400, height: 200 });
  const tall = backgroundImageRect(map, b, { width: 200, height: 400 });
  assert.deepEqual({ ...tall }, { left: 0, top: 150, width: 200, height: 100 });
  assert.equal(backgroundImageRect(map, b, { width: 0, height: 0 }), null);
});

test('stale/poor/repeated measurements cannot fabricate trail points', () => {
  const p = {
    mapId: 'room',
    mapVersion: 'v1',
    fresh: true,
    status: 'ok',
    position: { x: 1, y: 2 },
    sequence: 1,
    expiresAt: '2026-10-28T00:00:02Z',
  };
  const first = appendMeasuredTrail([], p, '');
  assert.equal(first.trail.length, 1);
  assert.equal(appendMeasuredTrail(first.trail, p, first.lastMeasurement).trail.length, 1);
  assert.equal(
    appendMeasuredTrail(first.trail, { ...p, fresh: false, sequence: 2 }, '').trail.length,
    1,
  );
  assert.equal(
    appendMeasuredTrail(first.trail, { ...p, status: 'low_quality' }, '').trail.length,
    1,
  );
  assert.equal(positionMatchesMap(p, map), true);
  assert.equal(positionMatchesMap({ ...p, mapVersion: 'v2' }, map), false);
});
