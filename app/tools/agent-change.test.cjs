const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('typescript');

function load(file, dependencies = {}) {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', 'src', file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText,
    {
      module,
      exports: module.exports,
      require: (name) => dependencies[name],
      setTimeout,
      clearTimeout,
      setInterval: () => 0,
      clearInterval: () => {},
      Date,
    },
  );
  return module.exports;
}

const evidence = {
  evidenceId: 'actions:2026-11-22:2',
  feature: 'actions',
  dimension: '动作',
  metric: '走动秒数',
  unit: 'seconds',
  date: '2026-11-22',
  slot: 2,
  slotLabel: '下午 12–18 点',
  slotStartAt: '2026-11-22T04:00:00.000Z',
  slotEndAt: '2026-11-22T10:00:00.000Z',
  value: 4200,
  coverageSeconds: 5400,
  coverageMinutes: 90,
  samples: 7,
  sampleStartDay: '2026-11-14',
  sampleEndDay: '2026-11-21',
  version: 'v1',
  revision: 3,
  range: { p10: 600, p50: 1200, p90: 2400 },
  direction: 'above',
};

test('基线未就绪时确认卡必须让主人明确选择', () => {
  const { requiresStrategyChoice } = load('services/agent-change.ts');
  assert.equal(requiresStrategyChoice({ baselineStrategyChoiceRequired: true }), true);
  assert.equal(requiresStrategyChoice({ baselineStrategyChoiceRequired: false }), false);
  assert.equal(requiresStrategyChoice({}), false);
  assert.equal(requiresStrategyChoice(null), false);
});

test('两项基线选择都有明确文案', () => {
  const { BASELINE_STRATEGY_OPTIONS, strategyLabel } = load('services/agent-change.ts');
  assert.deepEqual(
    Array.from(BASELINE_STRATEGY_OPTIONS, (option) => option.value),
    ['wait_baseline', 'fact_first'],
  );
  assert.ok(BASELINE_STRATEGY_OPTIONS.every((option) => option.label && option.hint));
  assert.equal(strategyLabel('wait_baseline'), '等基线就绪');
  assert.equal(strategyLabel('fact_first'), '先记录绝对事实');
  assert.equal(strategyLabel(null), '未选择');
});

test('冻结证据按单位展示数值与范围', () => {
  const { formatMetricValue, formatRangeValue, directionLabel } = load('services/agent-change.ts');
  assert.equal(formatMetricValue(evidence), '70 分钟');
  assert.equal(formatMetricValue({ ...evidence, unit: 'count', value: 9 }), '9 次');
  assert.equal(formatRangeValue(evidence.range, 'seconds'), '10 分钟 – 40 分钟');
  assert.equal(formatRangeValue(evidence.range, 'count'), '600 次 – 2400 次');
  assert.equal(formatRangeValue(null), '—');
  assert.equal(directionLabel('above'), '高于平时范围');
  assert.equal(directionLabel('below'), '低于平时范围');
  assert.equal(directionLabel(null), '在平时范围内');
});

test('冻结证据展示行顺序固定且含样本窗口与版本', () => {
  const { evidenceRows, evidenceWindow } = load('services/agent-change.ts');
  const rows = evidenceRows(evidence);
  assert.deepEqual(
    Array.from(rows, (row) => row.label),
    ['时段', '实际', '平时范围', '对比', '样本', '基线版本'],
  );
  assert.equal(rows[1].value, '70 分钟');
  assert.equal(rows[3].value, '高于平时范围（覆盖 90 分钟）');
  assert.equal(rows[4].value, '2026-11-14 – 2026-11-21（7 天）');
  assert.equal(rows[5].value, 'v1（第 3 次重算）');
  const win = evidenceWindow(evidence);
  assert.equal(win.startAt, '2026-11-22T04:00:00.000Z');
  assert.equal(win.endAt, '2026-11-22T10:00:00.000Z');
});

test('缺字段的证据安全降级（不显示 NaN）', () => {
  const { evidenceRows, formatMetricValue } = load('services/agent-change.ts');
  const rows = evidenceRows({ evidenceId: 'x', feature: 'sounds', date: '2026-11-22', slot: 0 });
  assert.equal(formatMetricValue({ value: null }), '无数据');
  assert.equal(rows[1].value, '无数据');
  assert.equal(rows[3].value, '在平时范围内（覆盖 0 分钟）');
});

test('降级回信卡的标题与说明跟是否带核对结果一致', () => {
  const { degradedLetterLabel, degradedLetterNotice } = load('services/agent-change.ts');
  // 带核对结果：不能再写「只包含覆盖与缺口说明」，否则与下面的「对比」行自相矛盾
  const withEvidence = degradedLetterNotice(true);
  assert.ok(withEvidence.includes('核对出的事实'));
  assert.ok(!withEvidence.includes('只包含覆盖与缺口说明'));
  assert.ok(withEvidence.includes('不代表它的整体状态正常或异常'));
  assert.equal(degradedLetterLabel(true), '委托回信（说明未完成）');
  // 没有核对结果：维持「未完成评估 / 只包含覆盖与缺口说明」
  assert.ok(degradedLetterNotice(false).includes('只包含覆盖与缺口说明'));
  assert.equal(degradedLetterLabel(false), '委托回信（未完成评估）');
});
