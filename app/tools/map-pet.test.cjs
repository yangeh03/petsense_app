const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
function load(file, dependencies = {}) {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/services', file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    { module, exports: module.exports, require: (name) => dependencies[name] },
  );
  return module.exports;
}
const { selectMapPet } = load('map-pet.ts', { '@/services/pet-avatar': load('pet-avatar.ts') });
const dashboard = {
  pet: { name: 'Marni', device_id: 'collar', avatar_template_id: 'marni' },
  avatarTemplate: {
    id: 'marni',
    behaviors: { walk: { gifUrl: '/walk.gif' }, sit: { gifUrl: '/sit.gif' } },
  },
};
const frame = (label, confidence = 1, deviceId = 'collar') => ({
  deviceId,
  edge: { action: { label, confidence } },
});
test('map follows the same confident edge action and media as dashboard', () => {
  assert.equal(selectMapPet(dashboard, frame('walking'), true).animationUrl, '/walk.gif');
  assert.equal(selectMapPet(dashboard, frame('sitting'), true).label, '坐着');
});
test('stale, unrecognized, low-confidence and other-device actions never animate a current pose', () => {
  for (const [value, fresh] of [
    [frame('walking'), false],
    [frame('unknown'), true],
    [frame('walking', 0.2), true],
    [frame('walking', 1, 'other'), true],
    [null, true],
  ]) {
    const result = selectMapPet(dashboard, value, fresh);
    assert.equal(result.animationUrl, null);
    assert.equal(result.label, '动作待识别');
    assert.equal(result.current, false);
  }
});
test('no pet never reuses another profile or invents standing', () => {
  assert.equal(selectMapPet(null, frame('walking'), true).animationUrl, null);
});
