const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
function load(file, dependencies = {}) {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(require('node:path').join(__dirname, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    { module, exports: module.exports, require: (name) => dependencies[name] },
  );
  return module.exports;
}
const view = () => load('../src/services/sound-view.ts');
const frame = (label, confidence = 1) => ({ edge: { sound: { label, confidence } } });
test('sound state is explicit even without a connection; stale quiet is never current silence', () => {
  const { soundStatus } = view();
  assert.equal(soundStatus(frame('quiet', 0), false, true).label, '声音未更新');
  assert.equal(soundStatus(null, false, false).label, '声音未连接');
  assert.equal(soundStatus(frame('quiet', 0), true, true).label, '安静');
});
test('sound distinguishes preparing, uncertain and vocal results without emotion guesses', () => {
  const { soundStatus } = view();
  assert.equal(soundStatus(frame('warming_up', 0), true, true).label, '声音识别准备中');
  assert.equal(soundStatus(frame('bark', 0.5), true, true).label, '声音暂未识别');
  assert.equal(soundStatus(frame('bark'), true, true).label, '正在吠叫');
  assert.equal(soundStatus(frame('howl'), true, true).label, '正在嚎叫');
  assert.equal(soundStatus(frame('whine'), true, true).label, '疑似呜咽');
  assert.equal(soundStatus(null, true, true).label, '声音暂未识别');
});
const event = (id, label, start, end = start) => ({
  id,
  label,
  startedAt: `2026-10-26T06:${start}Z`,
  lastSeenAt: `2026-10-26T06:${end}Z`,
  observedSeconds: 0,
  confidence: 0.9,
  frames: 1,
  experimental: label === 'whine',
});
test('nearby detections become one timestamped record rather than a pile of fragments', () => {
  const { groupSoundEvents } = view();
  const events = [event(3, 'howl', '04:00'), event(2, 'whine', '00:25'), event(1, 'bark', '00:00')];
  const before = JSON.stringify(events);
  const result = groupSoundEvents(events);
  assert.equal(result.length, 2);
  assert.equal(result[0].label, '嚎叫');
  assert.equal(result[1].label, '吠叫、疑似呜咽');
  assert.equal(result[1].startedAt, events[2].startedAt);
  assert.equal(result[1].lastSeenAt, events[1].lastSeenAt);
  assert.equal(result[1].duration, undefined);
  assert.equal(JSON.stringify(events), before);
});
test('merging uses last detection time, handles unsorted input, and excludes non-vocal labels', () => {
  const { groupSoundEvents } = view();
  const events = [
    event(2, 'bark', '01:20'),
    event(1, 'bark', '00:00', '01:00'),
    event(3, 'quiet', '01:25'),
  ];
  assert.equal(groupSoundEvents(events).length, 1);
  assert.equal(groupSoundEvents([...events, event(4, 'bark', '01:51')]).length, 2);
});
