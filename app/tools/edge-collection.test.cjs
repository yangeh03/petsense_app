const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id))
      nodes.set(id, {
        hidden: true,
        disabled: true,
        listeners: {},
        elements: { username: { value: 'owner' }, password: { value: 'test' } },
        addEventListener(event, fn) {
          this.listeners[event] = fn;
        },
      });
    return nodes.get(id);
  };
  let listener,
    now = 10000;
  const calls = [];
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, '../../server/petsense/telemetry/edge_collection.js'),
      'utf8',
    ),
    {
      document: { getElementById: node },
      window: {
        addEventListener: (_, fn) => {
          listener = fn;
        },
      },
      setInterval() {},
      AbortSignal: { timeout() {} },
      Date: { now: () => now },
      fetch: async (url, init) => {
        calls.push({ url, body: JSON.parse(init.body), headers: init.headers });
        return {
          ok: true,
          json: async () =>
            url.endsWith('/login') ? { token: 'test-session' } : { published: true },
        };
      },
    },
  );
  const frame = {
    boot_id: 'boot-a',
    seq: 1,
    sd_ready: true,
    utc_us: 123456,
    clock_age_us: 0,
    sd_free_bytes: 5000000,
    sd_total_bytes: 10000000,
    storage_draining: false,
    capture_active: false,
  };
  return {
    node,
    calls,
    frame,
    status: (payload = frame, online = true) => listener({ detail: { online, payload } }),
    tick: () => {
      now += 1100;
    },
  };
}
test('debug capture blocks old firmware, offline devices and full cards', () => {
  const f = fixture();
  f.status({});
  assert.equal(f.node('capture').disabled, true);
  f.status(f.frame, false);
  assert.equal(f.node('capture').disabled, true);
  f.status({ ...f.frame, sd_free_bytes: 0 });
  assert.equal(f.node('capture').disabled, true);
  f.status();
  assert.equal(f.node('capture').disabled, false);
  assert.match(f.node('storage').textContent, /4\.8 MiB/);
});
test('start requires login, debounces and waits for a matching device acknowledgement', async () => {
  const f = fixture();
  f.status();
  await f.node('capture').listeners.click();
  assert.equal(f.calls.length, 0);
  assert.equal(f.node('captureLogin').hidden, false);
  await f
    .node('captureLogin')
    .listeners.submit({ preventDefault() {}, currentTarget: f.node('captureLogin') });
  assert.equal(f.calls[0].url, '/api/app/auth/login');
  f.tick();
  await f.node('capture').listeners.click();
  await f.node('capture').listeners.click();
  assert.equal(f.calls.length, 3);
  assert.equal(f.calls[2].headers['X-Petsense-Session'], 'test-session');
  assert.equal(f.node('capture').disabled, true);
  const session = f.calls[1].body.id;
  f.status({ ...f.frame, seq: 2, capture_active: true, session_id: 'wrong' });
  assert.equal(f.node('capture').disabled, true);
  f.status({ ...f.frame, seq: 3, capture_active: true, session_id: session });
  assert.equal(f.node('capture').disabled, false);
  assert.match(f.node('captureMessage').textContent, /已确认开始/);
  f.tick();
  await f.node('capture').listeners.click();
  f.status({ ...f.frame, seq: 4, storage_draining: true });
  assert.equal(f.node('capture').disabled, true);
  f.status({ ...f.frame, seq: 5 });
  assert.match(f.node('captureMessage').textContent, /已确认停止/);
});
