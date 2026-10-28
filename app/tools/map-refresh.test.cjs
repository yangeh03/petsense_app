const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

class ApiError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}
const modules = { '@/services/api': { ApiError } };
function load(name, file) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { exports, atob, require: (key) => modules[key] });
  modules[name] = exports;
  return exports;
}
load('@/services/map-image', '../src/services/map-image.ts');
load('@/services/map-viewport', '../src/services/map-viewport.ts');
const { MapRefresh } = load('@/services/map-refresh', '../src/services/map-refresh.ts');
const asset = path.join(__dirname, '../../server/petsense/uwb/assets/reconstruction-room-v1');
const original = JSON.parse(fs.readFileSync(`${asset}/map.json`, 'utf8'));
original.revision = 'first';
original.background.imageDataUri = `data:image/png;base64,${fs.readFileSync(`${asset}/floorplan.png`).toString('base64')}`;
const signal = new AbortController().signal;
function fixture() {
  const state = { map: null, versions: 0, images: 0, status: 200 };
  const loader = new MapRefresh(async (url) => {
    if (url.startsWith('/uwb/map-version?')) {
      state.versions++;
      if (state.status !== 200) throw new ApiError(state.status);
      const m = state.map;
      return {
        deviceId: 'collar',
        map: m ? { mapId: m.mapId, version: m.version, revision: m.revision } : null,
      };
    }
    state.images++;
    return { deviceId: 'collar', map: state.map };
  });
  return { state, loader };
}

test('new bindings, version changes and removals refresh without any position data', async () => {
  const { state, loader } = fixture();
  assert.equal(await loader.refresh('collar', null, signal, 0), null);
  assert.equal(state.images, 0);
  state.map = original;
  assert.equal(await loader.refresh('collar', null, signal, 4999), null);
  assert.equal((await loader.refresh('collar', null, signal, 5000)).version, 'v1');
  const cached = await loader.refresh('collar', null, signal, 10000);
  assert.equal(cached.revision, 'first');
  assert.equal(state.images, 1);
  state.map = { ...original, version: 'v2', revision: 'second' };
  assert.equal((await loader.refresh('collar', null, signal, 15000)).version, 'v2');
  assert.equal(state.images, 2);
  state.map = null;
  assert.equal(await loader.refresh('collar', null, signal, 20000), null);
});

test('Tag binding revision changes refresh even when map ID/version stay the same', async () => {
  const { state, loader } = fixture();
  state.map = original;
  await loader.refresh('collar', null, signal, 0);
  state.map = { ...original, revision: 'another-tag' };
  assert.equal((await loader.refresh('collar', null, signal, 5000)).revision, 'another-tag');
  assert.equal(state.images, 2);
});

test('a stale position from a previous map does not repeatedly download the PNG', async () => {
  const { state, loader } = fixture();
  state.map = original;
  await loader.refresh('collar', null, signal, 0);
  const p = { mapId: original.mapId, mapVersion: 'old' };
  await loader.refresh('collar', p, signal, 1000);
  await loader.refresh('collar', p, signal, 2000);
  assert.equal(state.images, 1);
  assert.equal(state.versions, 2);
});

test('old servers use a 30-second full-map fallback; other HTTP errors are not treated as legacy', async () => {
  const { state, loader } = fixture();
  state.status = 503;
  await assert.rejects(loader.refresh('collar', null, signal, 0), /503/);
  assert.equal(state.images, 0);
  state.status = 404;
  state.map = original;
  await loader.refresh('collar', null, signal, 0);
  await loader.refresh('collar', null, signal, 5000);
  assert.equal(state.images, 1);
  await loader.refresh('collar', null, signal, 30000);
  assert.equal(state.images, 2);
  assert.equal(state.versions, 2);
});

test('invalid replacement images do not poison the cache and clear forces a fresh check', async () => {
  const { state, loader } = fixture();
  state.map = original;
  await loader.refresh('collar', null, signal, 0);
  state.map = {
    ...original,
    revision: 'broken',
    background: { ...original.background, crc32: '00000000' },
  };
  await assert.rejects(loader.refresh('collar', null, signal, 5000), /校验失败/);
  state.map = { ...original, revision: 'fixed' };
  assert.equal((await loader.refresh('collar', null, signal, 5001)).revision, 'fixed');
  loader.clear();
  state.map = null;
  assert.equal(await loader.refresh('collar', null, signal, 5002), null);
});
