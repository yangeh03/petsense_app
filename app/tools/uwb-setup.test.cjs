const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const exportsObject = {};
const code = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../src/services/uwb-setup.ts'), 'utf8'),
  {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  },
).outputText;
vm.runInNewContext(code, { exports: exportsObject });
const { canPublishPreview } = exportsObject;

test('only an eligible preview from the selected alignment job can publish', () => {
  const preview = { jobId: 'new-job', candidate: { id: 'mirror', eligible: true } };
  assert.equal(canPublishPreview(null, 'new-job', 'mirror'), false);
  assert.equal(canPublishPreview(preview, 'old-job', 'mirror'), false);
  assert.equal(canPublishPreview(preview, 'new-job', 'normal'), false);
  assert.equal(canPublishPreview(preview, 'new-job', 'mirror'), true);
  preview.candidate.eligible = false;
  assert.equal(canPublishPreview(preview, 'new-job', 'mirror'), false);
});
