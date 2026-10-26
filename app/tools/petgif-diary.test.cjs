const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function load(api, uploads = {}) {
  const module = { exports: {} };
  const dependencies = {
    '@/services/api': api,
    '@/config': { API_BASE_URL: 'http://localhost:8080' },
    '@/services/form-upload': {
      buildUploadForm: async () => new FormData(),
      IS_WEB_LIKE: false,
      sendMultipart:
        uploads.sendMultipart ?? (() => Promise.reject(new Error('unexpected multipart'))),
    },
  };
  vm.runInNewContext(
    ts.transpileModule(
      fs.readFileSync(require('node:path').join(__dirname, '../src/services/petgif.ts'), 'utf8'),
      {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      },
    ).outputText,
    {
      module,
      exports: module.exports,
      require: (name) => dependencies[name],
      AbortController,
      AbortSignal,
      FormData,
      setTimeout,
      clearTimeout,
    },
  );
  return module.exports;
}

const job = {
  jobId: 'job-1',
  petId: 9,
  status: 'queued',
  createdAt: 'now',
  updatedAt: 'now',
};

function loadValidation() {
  const module = { exports: {} };
  vm.runInNewContext(
    ts.transpileModule(
      fs.readFileSync(
        require('node:path').join(__dirname, '../src/services/diary-validation.ts'),
        'utf8',
      ),
      { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
    ).outputText,
    { module, exports: module.exports },
  );
  return module.exports;
}

test('diary requires explicitly entered steps while accepting explicit zero', () => {
  const { validateDiaryDraft } = loadValidation();
  const draft = { durationMin: '25', steps: '', places: '', notes: '' };
  assert.equal(validateDiaryDraft(draft), null);
  assert.deepEqual(JSON.parse(JSON.stringify(validateDiaryDraft({ ...draft, steps: '0' }))), {
    durationMin: 25,
    steps: 0,
    places: '',
    notes: '',
  });
});

test('diary submission sends an idempotency key and returns immediately from the job API', async () => {
  let call;
  const api = {
    ApiError: class ApiError extends Error {},
    loadToken: async () => 'session',
    request: async (...args) => {
      call = args;
      return job;
    },
  };
  const service = load(api);
  const result = await service.createDiaryJob(
    9,
    {
      durationMin: 20,
      steps: 1200,
      places: '公园',
      notes: '',
    },
    'idem-123',
  );
  assert.equal(result.jobId, 'job-1');
  assert.equal(call[0], '/petgif/pets/9/diaries');
  assert.equal(call[1].headers['Idempotency-Key'], 'idem-123');
  assert.equal(
    JSON.stringify(call[1].body),
    JSON.stringify({
      durationMin: 20,
      steps: 1200,
      places: '公园',
      notes: '',
    }),
  );
  assert.ok(call[1].signal instanceof AbortSignal);
});

test('native multipart uses the same idempotency key and bounded timeout', async () => {
  let call;
  const api = {
    ApiError: class ApiError extends Error {},
    loadToken: async () => 'session',
    request: async () => job,
  };
  const service = load(api, {
    sendMultipart: async (...args) => {
      call = args;
      return { status: 202, body: JSON.stringify(job) };
    },
  });
  const result = await service.createDiaryJob(
    9,
    {
      durationMin: 20,
      steps: 1200,
      places: '',
      notes: '',
      photos: [{ uri: 'file:///walk.png', name: 'walk.png', type: 'image/png' }],
    },
    'idem-photo',
  );
  assert.equal(result.status, 'queued');
  assert.equal(call[0], 'http://localhost:8080/api/app/petgif/pets/9/diaries');
  assert.equal(call[1].headers['Idempotency-Key'], 'idem-photo');
  assert.equal(call[1].timeoutMs, 30_000);
  assert.equal(call[1].files[0].field, 'photos');
});

test('status and recent-history requests are authenticated and abortable', async () => {
  const calls = [];
  const api = {
    ApiError: class ApiError extends Error {},
    loadToken: async () => null,
    request: async (...args) => {
      calls.push(args);
      return { jobs: [] };
    },
  };
  const service = load(api);
  const controller = new AbortController();
  await service.getDiaryJob('job/1', controller.signal);
  await service.listDiaryJobs(9, controller.signal);
  assert.equal(calls[0][0], '/petgif/diary-jobs/job%2F1');
  assert.equal(calls[1][0], '/petgif/pets/9/diary-jobs');
  assert.equal(calls[0][1].signal, controller.signal);
  assert.equal(calls[1][1].signal, controller.signal);
});
