const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

function load(file, requireStub) {
  const exports = {};
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    { exports, require: requireStub },
  );
  return exports;
}

// 读取生成文件里的 JSON 字面量（< 已转义为 \u003c）。
function snapshotData() {
  const source = fs.readFileSync(path.join(__dirname, '../src/generated/dog-map-data.ts'), 'utf8');
  const literal = source.slice(source.indexOf('= ') + 2).replace(/;\s*$/, '');
  return JSON.parse(literal.replace(/\\u003c/g, '<'));
}

test('bundled dog map snapshot keeps provenance and version metadata', () => {
  const data = snapshotData();
  assert.match(data.version, /^nansha-\d{4}-\d{2}-\d{2}$/);
  assert.equal(data.capturedAt, '2026-11-20');
  assert.equal(data.source.name, 'CityScene 南沙演示地图');
  assert.equal(data.source.url, 'https://qioyo.site/petsenseIMG/dogmap/');
  assert.match(data.source.crs, /GCJ-02/);
  assert.match(data.source.crs, /WGS-84/);
});

test('nansha-2026-11-20 snapshot has the full 663 verified rows', () => {
  const data = snapshotData();
  assert.equal(data.points.length, 663);
  const lons = data.points.map((row) => row[0]);
  const lats = data.points.map((row) => row[1]);
  assert.equal(data.coverage.west, Math.min(...lons));
  assert.equal(data.coverage.east, Math.max(...lons));
  assert.equal(data.coverage.south, Math.min(...lats));
  assert.equal(data.coverage.north, Math.max(...lats));
  for (const row of data.points) {
    const [lon, lat, name, score, grade, gvi, sidewalk, traffic, pedestrian] = row;
    assert.ok(typeof name === 'string' && name.length > 0);
    assert.ok(Number.isInteger(score) && score >= 0 && score <= 100, name);
    assert.ok(['适合遛狗', '一般', '较差', '不适合遛狗'].includes(grade), name);
    assert.ok(lon > 113.2 && lon < 113.8 && lat > 22.5 && lat < 23.05, name);
    for (const metric of [gvi, sidewalk, traffic, pedestrian]) {
      assert.ok(metric >= 0 && metric <= 100, name);
    }
  }
});

test('source grades and spot-checked rows are preserved verbatim, not recomputed', () => {
  const data = snapshotData();
  const byName = Object.fromEntries(data.points.map((row) => [row[2], row]));
  assert.deepEqual(byName['D49号路'], [
    113.542658,
    22.795723,
    'D49号路',
    70,
    '适合遛狗',
    13,
    6,
    34,
    0,
    '绿地环绕',
  ]);
  assert.deepEqual(byName['M90乡道'], [
    113.471369,
    22.940818,
    'M90乡道',
    50,
    '较差',
    40,
    1.1,
    27,
    0.3,
    '绿地环绕、农田风光、滨水、林荫',
  ]);
});

test('loader expands tuples into typed points with display names and tags', () => {
  const data = snapshotData();
  const stubs = {
    '../generated/dog-map-data': { DOG_MAP_SNAPSHOT: data },
  };
  const { DOG_MAP, expandDogMapPoint, dogMapPointName } = load('../src/data/dog-map.ts', (id) => {
    const stub = stubs[id];
    if (!stub) throw new Error(`Unexpected import ${id}`);
    return stub;
  });
  assert.equal(DOG_MAP.points.length, data.points.length);
  assert.equal(DOG_MAP.version, data.version);
  const sample = DOG_MAP.points.find((point) => point.name === 'D49号路');
  assert.equal(sample.score, 70);
  assert.equal(sample.grade, '适合遛狗');
  assert.equal(sample.gviPercent, 13);
  assert.equal(sample.sidewalkPercent, 6);
  assert.deepEqual([...sample.tags], ['绿地环绕']);
  assert.deepEqual(dogMapPointName(sample), 'D49号路');
  const unnamed = expandDogMapPoint([
    113.5,
    22.8,
    'unnamed_42114_8647',
    47,
    '较差',
    2,
    0,
    44,
    0.1,
    '宽敞道路',
  ]);
  assert.equal(dogMapPointName(unnamed), '未命名道路');
  assert.deepEqual([...unnamed.tags], ['宽敞道路']);
  const empty = expandDogMapPoint([113.5, 22.8, 'x', 1, '较差', 0, 0, 0, 0, '']);
  assert.deepEqual([...empty.tags], []);
});
