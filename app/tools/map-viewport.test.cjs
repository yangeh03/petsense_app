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
const { clampMapPan, mapBounds, mapImageRect, appendMeasuredTrail, positionMatchesMap } =
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

test('raster screen rectangle shares the overlay fit, offsets, aspect ratio and upward map Y', () => {
  const fitted = mapImageRect(map, { width: 400, height: 300 });
  assert.equal(fitted.left, 0);
  assert.equal(fitted.top, 50);
  assert.equal(fitted.width, 400);
  assert.equal(fitted.height, 200);
  // An anchor outside the raster expands the view to 8 x 4 metres.
  const expanded = { ...map, anchors: [{ id: 0, position: [6, 3] }] };
  const rect = mapImageRect(expanded, { width: 400, height: 200 });
  assert.equal(rect.left, 0);
  assert.equal(rect.top, 100);
  assert.equal(rect.width, 200);
  assert.equal(rect.height, 100);
  // Physical point (0,0) is pixel (100,150) in both image and SVG projections.
  assert.equal(rect.left + 200 * (rect.width / 400), 100);
  assert.equal(rect.top + 100 * (rect.height / 200), 150);
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
