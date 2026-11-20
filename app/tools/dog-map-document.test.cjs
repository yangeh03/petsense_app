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

const { dogMapDocument } = load('../src/services/dog-map-document.ts', (id) => {
  if (id === '@/generated/leaflet-assets') return { leafletJs: '/*LEAFLET_JS*/', leafletCss: '' };
  throw new Error(`Unexpected import ${id}`);
});

function document(overrides = {}) {
  return dogMapDocument({
    tint: '#E8835C',
    background: '#F3EAE0',
    textSecondary: '#94836F',
    tileUrl: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    coverage: { west: 113.3, east: 113.64, south: 22.63, north: 22.96 },
    points: [
      { longitude: 113.542658, latitude: 22.795723, score: 70 },
      { longitude: 113.471369, latitude: 22.940818, score: 50 },
    ],
    ...overrides,
  });
}

test('dog map document keeps Leaflet local and never loads external scripts', () => {
  const html = document();
  assert.match(html, /\/\*LEAFLET_JS\*\//);
  assert.doesNotMatch(html, /<script src=/);
  assert.match(html, /Content-Security-Policy/);
  // 不访问上游评分页面。
  assert.ok(!html.includes('qioyo.site'));
});

test('dog map document uses WGS84 tiles with attribution and fits coverage on entry', () => {
  const html = document();
  assert.match(html, /https:\/\/tile\.openstreetmap\.org\/\{z\}\/\{x\}\/\{y\}\.png/);
  assert.match(html, /OpenStreetMap<\/a> contributors/);
  assert.match(html, /map\.fitBounds\(bounds,\{padding:\[24,24\]\}\)/);
  assert.match(html, /"coverage":\{"west":113\.3,"east":113\.64,"south":22\.63,"north":22\.96\}/);
  assert.match(
    html,
    /bounds=L\.latLngBounds\(\[\[config\.coverage\.south,config\.coverage\.west\]/,
  );
});

test('dog map document embeds score points and aggregates dense markers at low zoom', () => {
  const html = document();
  assert.match(html, /\[\[113\.542658,22\.795723,70\],\[113\.471369,22\.940818,50\]\]/);
  assert.match(html, /DETAIL_ZOOM=16/);
  assert.match(html, /Math\.floor\(pt\.x\/CELL\)/);
  assert.match(html, /window\.petSenseDogReset/);
  assert.match(html, /window\.petSenseDogFocus/);
  assert.match(html, /notify\('select',index\)/);
});

test('dog map document reports tile failure and coverage state to the app', () => {
  const html = document();
  assert.match(html, /notify\('tile-error'\)/);
  assert.match(html, /notify\('tiles-ready'\)/);
  assert.match(html, /'in-coverage':'out-of-coverage'/);
  assert.match(html, /padded\.contains\(map\.getCenter\(\)\)/);
});
