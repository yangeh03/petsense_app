const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const { test } = require('node:test');
const exportsObject = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(
      require('node:path').join(__dirname, '../src/services/region-label.ts'),
      'utf8',
    ),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } },
  ).outputText,
  { exports: exportsObject },
);
const label = exportsObject.regionLabel;
const map = { mapId: 'room', version: 'v1', regionsRevision: 'a' };
const position = {
  mapId: 'room',
  mapVersion: 'v1',
  regionsRevision: 'a',
  position: { x: 1, y: 2 },
  region: { id: 'sofa', label: '沙发前' },
  regionStatus: 'known',
};
test('named area is current only for a matching map and live fix', () => {
  assert.equal(label(position, map, true), '沙发前');
  assert.equal(label(position, map, false), '上次所在：沙发前');
  assert.equal(label(position, { ...map, regionsRevision: 'b' }, true), '区域信息更新中');
  assert.equal(label(position, { ...map, version: 'v2' }, true), '区域信息更新中');
  assert.equal(label(null, map, true), '等待定位');
});
test('uncertainty and transitions do not claim the old area as current', () => {
  assert.equal(label({ ...position, regionStatus: 'transition' }, map, true), '区域边界附近');
  assert.equal(
    label({ ...position, regionStatus: 'uncertain' }, map, true),
    '定位质量不足，区域待确认',
  );
  assert.equal(
    label({ ...position, region: null, regionStatus: 'unmapped' }, map, true),
    '未命名区域',
  );
  assert.equal(
    label({ ...position, region: null, regionStatus: 'unconfigured' }, map, true),
    '尚未设置区域',
  );
});
