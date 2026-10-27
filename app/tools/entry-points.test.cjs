const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const read = (file) => fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');

test('publishing is a community action rather than a global floating button', () => {
  const layout = read('app/(tabs)/_layout.tsx');
  const community = read('app/(tabs)/community.tsx');
  const create = read('app/create.tsx');

  assert.doesNotMatch(layout, /createButton/);
  assert.match(community, /发布动态/);
  assert.match(community, /router\.push\('\/create'\)/);
  assert.doesNotMatch(create, /宠物档案/);
});
