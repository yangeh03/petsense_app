const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

test('profile prioritizes pet care and collar connection before secondary tools', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/app/(tabs)/profile.tsx'), 'utf8');
  const pet = source.indexOf('宠物档案');
  const device = source.indexOf('项圈与连接');
  const social = source.indexOf('社区与内容');
  const advanced = source.indexOf('实验与高级');

  assert.ok(pet >= 0 && pet < device);
  assert.ok(device < social);
  assert.ok(social < advanced);
  assert.match(source, /设备诊断（高级）/);
});
