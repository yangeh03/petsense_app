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

const alertAt = (ruleId, severity = 'warning') => ({
  ruleId,
  label: ruleId,
  severity,
  triggeredAt: 0,
  detail: '',
  acknowledged: false,
});

test('suppressManagedAlerts drops only the three migrated rules when backend manages them', () => {
  const { suppressManagedAlerts, CARE_RULE_TO_ALERT_RULE } = load('services/care-rules.ts', {
    '@/services/api': { request: async () => {} },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(CARE_RULE_TO_ALERT_RULE)), {
    battery_low: 'batteryLow',
    data_stale: 'dataStale',
    readings_invalid: 'sensorOff',
  });
  const alerts = [
    alertAt('batteryLow'),
    alertAt('dataStale', 'info'),
    alertAt('sensorOff'),
    alertAt('heartRateHigh', 'critical'),
    alertAt('spo2Low', 'critical'),
  ];
  const kept = suppressManagedAlerts(alerts, ['battery_low', 'data_stale', 'readings_invalid']);
  assert.deepEqual(
    kept.map((alert) => alert.ruleId),
    ['heartRateHigh', 'spo2Low'],
    '受管理三类被抑制，其他告警保留',
  );
});

test('unconfirmed or empty capability keeps local alerts (fallback for old clients)', () => {
  const { suppressManagedAlerts } = load('services/care-rules.ts', {
    '@/services/api': { request: async () => {} },
  });
  const alerts = [alertAt('batteryLow'), alertAt('dataStale'), alertAt('sensorOff')];
  assert.equal(suppressManagedAlerts(alerts, null).length, 3, '未确认能力时不抑制');
  assert.equal(suppressManagedAlerts(alerts, []).length, 3, '后端声明不接管时不抑制');
});

test('refreshCareRules maps capability and clears on failure per pet', async () => {
  let mode = 'ok';
  const fetched = [];
  const { refreshCareRules, clearCareRules, managedCareRules } = load('services/care-rules.ts', {
    '@/services/api': {
      request: async (url) => {
        fetched.push(url);
        if (mode === 'fail') throw new Error('network down');
        return {
          capability: 'care-v1',
          managedRules: ['battery_low', 'data_stale', 'readings_invalid', 'bogus'],
        };
      },
    },
  });
  await refreshCareRules(7);
  assert.deepEqual(fetched, ['/agent/pets/7/care/rules']);
  assert.deepEqual(
    managedCareRules(),
    ['battery_low', 'data_stale', 'readings_invalid'],
    '未知规则被过滤',
  );

  mode = 'fail';
  await refreshCareRules(8); // 换宠：新探测失败 → 恢复本地逻辑
  assert.equal(managedCareRules(), null);

  mode = 'ok';
  await refreshCareRules(9);
  assert.equal(managedCareRules().length, 3);
  clearCareRules(); // 退出/切宠
  assert.equal(managedCareRules(), null);
});

test('refreshCareRules(null) clears without touching the network', async () => {
  const { refreshCareRules, managedCareRules } = load('services/care-rules.ts', {
    '@/services/api': {
      request: async () => {
        throw new Error('should not be called');
      },
    },
  });
  await refreshCareRules(null);
  assert.equal(managedCareRules(), null);
});
