const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

function load(
  file,
  requireStub = () => {
    throw new Error('Unexpected import');
  },
) {
  const exports = {};
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    { exports, require: requireStub },
  );
  return exports;
}
const { gpsView } = load('../src/services/gps-view.ts');
const now = Date.parse('2026-10-29T08:00:00Z');
function frame(change = {}) {
  return {
    deviceId: 'collar',
    receivedAt: new Date(now).toISOString(),
    gps: {
      valid: true,
      latitude: 0,
      longitude: 0,
      utc: '2026-10-29T08:00:00Z',
      remainingMs: 13000,
    },
    ...change,
  };
}
test('GPS is scoped to the selected collar and never substitutes phone/replay coordinates', () => {
  assert.equal(gpsView(null, frame(), null, now).point, null);
  assert.equal(gpsView('other', frame(), null, now).point, null);
  assert.equal(gpsView('collar', frame(), null, now, true).point, null);
  assert.equal(gpsView('collar', frame(), null, now).point.latitude, 0);
});
test('GPS expires without another telemetry frame; future clock errors are not shown', () => {
  assert.notEqual(gpsView('collar', frame(), null, now + 12999).point, null);
  assert.equal(gpsView('collar', frame(), null, now + 13000).point, null);
  assert.equal(gpsView('collar', frame(), null, now - 6000).point, null);
});
test('a current REST snapshot initializes GPS and newer no-fix telemetry clears it', () => {
  const snapshot = frame({ receivedAt: new Date(now - 1000).toISOString() });
  assert.notEqual(gpsView('collar', null, snapshot, now).point, null);
  const searching = frame({
    gps: {
      valid: false,
      status: 'searching',
      latitude: null,
      longitude: null,
      utc: null,
      remainingMs: 0,
    },
  });
  assert.equal(gpsView('collar', searching, snapshot, now).point, null);
  assert.match(gpsView('collar', searching, snapshot, now).status, /搜星/);
  assert.equal(gpsView('collar', frame({ gps: undefined }), snapshot, now).point, null);
});

const assets = load('../src/generated/leaflet-assets.ts');
const { gpsMapDocument } = load('../src/services/gps-map-document.ts', () => assets);
test('local Leaflet map follows real updates, allows panning and recenters on demand', () => {
  const html = gpsMapDocument(
    '#E8835C',
    '#F3EAE0',
    'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  );
  assert.doesNotMatch(html, /<script src=/);
  assert.match(html, /OpenStreetMap/);
  const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
  const events = {},
    sent = [],
    centers = [],
    pans = [];
  let marker = null;
  const map = {
    on: (name, callback) => {
      events[name] = callback;
    },
    setView: (pos) => centers.push(pos),
    panTo: (pos) => pans.push(pos),
    invalidateSize() {},
  };
  const parent = { postMessage: (value) => sent.push(JSON.parse(value).type) };
  const window = { parent, addEventListener() {} };
  const L = {
    map: () => map,
    tileLayer: () => ({
      addTo() {
        return this;
      },
      on() {},
    }),
    circleMarker: (pos) => {
      marker = {
        pos,
        addTo() {
          return this;
        },
        setLatLng(p) {
          this.pos = p;
        },
        getLatLng() {
          return this.pos;
        },
      };
      return marker;
    },
  };
  vm.runInNewContext(script, {
    window,
    document: { addEventListener() {} },
    L,
    setTimeout: () => 1,
    clearTimeout() {},
  });
  assert.equal(marker, null);
  window.petSenseGps({ latitude: NaN, longitude: 0 });
  assert.equal(marker, null);
  window.petSenseGps({ latitude: 31.2223881, longitude: 121.3539011 });
  assert.equal(centers.length, 1);
  window.petSenseGps({ latitude: 31.2224, longitude: 121.354 });
  assert.equal(pans.length, 1);
  events.dragstart();
  window.petSenseGps({ latitude: 31.2225, longitude: 121.355 });
  assert.equal(pans.length, 1);
  assert.equal(marker.pos[1], 121.355);
  window.petSenseGpsCenter();
  assert.equal(centers.length, 2);
  assert.equal(sent[0], 'ready');
});
