const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

function load(storage) {
  const exports = {};
  vm.runInNewContext(
    ts.transpileModule(
      fs.readFileSync(path.join(__dirname, '../src/services/location-mode.ts'), 'utf8'),
      { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
    ).outputText,
    {
      exports,
      require: () => storage,
    },
  );
  return exports;
}

test('location mode defaults to indoor and only accepts explicit outdoor', () => {
  const { parseLocationMode } = load({});
  assert.equal(parseLocationMode(null), 'indoor');
  assert.equal(parseLocationMode(undefined), 'indoor');
  assert.equal(parseLocationMode(''), 'indoor');
  assert.equal(parseLocationMode('telemetry'), 'indoor');
  assert.equal(parseLocationMode('indoor'), 'indoor');
  assert.equal(parseLocationMode('outdoor'), 'outdoor');
});

test('load falls back to indoor when storage fails; save swallows failures', async () => {
  const throwing = {
    getItem: () => {
      throw new Error('storage broken');
    },
    setItem: () => {
      throw new Error('storage broken');
    },
  };
  const failing = load(throwing);
  assert.equal(await failing.loadLocationMode(), 'indoor');
  await assert.doesNotReject(() => failing.saveLocationMode('outdoor'));

  let stored = null;
  const working = load({
    getItem: async () => stored,
    setItem: async (_key, value) => {
      stored = value;
    },
  });
  assert.equal(await working.loadLocationMode(), 'indoor');
  await working.saveLocationMode('outdoor');
  assert.equal(await working.loadLocationMode(), 'outdoor');
  assert.equal(await failing.loadLocationMode(), 'indoor');
});
