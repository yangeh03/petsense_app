const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const navigationPath = path.join(__dirname, '../src/constants/tab-navigation.ts');
const recordsPath = path.join(__dirname, '../src/app/(tabs)/records.tsx');

test('shared navigation defines the five owner-facing tabs in order', () => {
  assert.ok(fs.existsSync(navigationPath), 'expected a shared tab navigation module');
  const ts = require('typescript');
  const source = fs.readFileSync(navigationPath, 'utf8');
  const module = { exports: {} };
  require('node:vm').runInNewContext(
    ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    { module, exports: module.exports },
  );
  const { APP_TABS } = module.exports;
  assert.deepEqual(
    Array.from(APP_TABS, (tab) => tab.name),
    ['index', 'records', 'tracking', 'community', 'profile'],
  );
  assert.deepEqual(
    Array.from(APP_TABS, (tab) => tab.label),
    ['今日', '记录', '位置', '社区', '我的'],
  );
  assert.deepEqual(
    Array.from(APP_TABS, (tab) => tab.href),
    ['/', '/records', '/tracking', '/community', '/profile'],
  );
  for (const tab of APP_TABS) {
    assert.equal(typeof tab.icon.ios, 'string');
    assert.equal(typeof tab.icon.android, 'string');
    assert.equal(typeof tab.icon.web, 'string');
  }
});

test('records tab has its own route instead of hiding history on today', () => {
  assert.ok(fs.existsSync(recordsPath), 'expected a dedicated records tab route');
  const recordsSource = fs.readFileSync(recordsPath, 'utf8');
  const todaySource = fs.readFileSync(path.join(__dirname, '../src/app/(tabs)/index.tsx'), 'utf8');

  assert.match(recordsSource, /TrendPanel/, 'expected records to own health trends');
  assert.match(recordsSource, /SoundTimeline/, 'expected records to own sound history');
  assert.match(recordsSource, /DiaryModal/, 'expected records to own the activity diary');
  assert.match(todaySource, /router\.push\('\/records'\)/, 'expected today to link to records');
});
