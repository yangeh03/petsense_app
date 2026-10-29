const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');
const exportsObject = {};
vm.runInNewContext(
  ts.transpileModule(
    fs.readFileSync(path.join(__dirname, '../src/services/display-plan.ts'), 'utf8'),
    {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    },
  ).outputText,
  { exports: exportsObject },
);
const { displayPlan } = exportsObject;
const plan = {
  schemaVersion: 1,
  deviceId: 'collar',
  planId: 'room',
  revision: 'v1',
  name: '场地',
  units: 'relative',
  calibrationStatus: 'required',
  image: { width: 1009, height: 558, imageDataUri: 'data:image/png;base64,iVBORw0KGgo=' },
};
test('relative display plan is scoped to the selected device', () => {
  assert.equal(displayPlan(plan, 'collar').planId, 'room');
  assert.equal(displayPlan(plan, 'other'), null);
  assert.equal(displayPlan(null, 'collar'), null);
});
test('malformed images and metric plans cannot masquerade as display-only plans', () => {
  assert.equal(displayPlan({ ...plan, units: 'm' }, 'collar'), null);
  assert.equal(displayPlan({ ...plan, image: { ...plan.image, width: -1 } }, 'collar'), null);
  assert.equal(
    displayPlan(
      { ...plan, image: { ...plan.image, imageDataUri: 'https://example.com/private' } },
      'collar',
    ),
    null,
  );
});
