const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const url = process.env.EXPO_PUBLIC_TELEMETRY_URL || 'ws://81.71.71.31/ws';
const source = fs.readFileSync(
  path.join(__dirname, '../src/services/telemetry-service.ts'),
  'utf8',
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = {
  exports: {},
  require: (name) => {
    assert.equal(name, '@/config');
    return { TELEMETRY_WS_URL: url };
  },
  WebSocket,
  setTimeout,
  clearTimeout,
};
vm.runInNewContext(compiled, context);
const { parseTelemetryMessage, telemetryService } = context.exports;
for (const input of ['null', '{}', '{', '{"topic":42}', '{"topic":{}}']) {
  assert.equal(parseTelemetryMessage(input), null);
}
const packet = {
  topic: 'petsense/sensor/esp32-001',
  received_at: new Date().toISOString(),
  payload: {
    deviceId: 'test',
    ts_ms: 100,
    battery: { ok: true, found: true, percent: 80, voltage: 4 },
  },
};
assert.equal(parseTelemetryMessage(JSON.stringify(packet)).battery.percent, 80);
assert.equal(
  parseTelemetryMessage(JSON.stringify({ ...packet, payload: JSON.stringify(packet.payload) }))
    .tsMs,
  100,
);
assert.equal(
  parseTelemetryMessage(
    JSON.stringify({ ...packet, payload: { ...packet.payload, battery: { percent: 'bad' } } }),
  ),
  null,
);
assert.equal(
  parseTelemetryMessage(JSON.stringify({ ...packet, topic: 'petsense/device/esp32-001/command' })),
  null,
);
console.log('Parser checks passed');

if (process.argv.includes('--live')) {
  let count = 0;
  let first;
  const timer = setTimeout(() => {
    console.error('No advancing live device stream within 20s');
    stop();
    process.exitCode = 1;
  }, 20000);
  const stop = telemetryService.subscribeFrame((frame) => {
    assert.ok(frame.imu?.ok && frame.audio?.ok && frame.battery?.found);
    assert.equal(frame.audio.mel.length, 40);
    first ??= frame;
    count++;
    if (count >= 20 && frame.tsMs > first.tsMs) {
      console.log(
        JSON.stringify({
          url,
          deviceId: frame.deviceId,
          frames: count,
          deviceSpanMs: frame.tsMs - first.tsMs,
          receivedAt: frame.receivedAt,
          sampleRate: frame.audio.sampleRate,
          sdLogging: frame.experiment?.sdLogging,
        }),
      );
      stop();
      assert.equal(telemetryService.getStatus(), 'closed');
      clearTimeout(timer);
    }
  });
}
