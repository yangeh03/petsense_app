/** 用仓库自带的 typescript 转译 TS 模块并在 vm 里执行（与 care-rules 测试同一手法）。 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(file, dependencies = {}) {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', '..', 'src', file), 'utf8'), {
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
      AbortController,
      URLSearchParams,
    },
  );
  return module.exports;
}

module.exports = { load };
