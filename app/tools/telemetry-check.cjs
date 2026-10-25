const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const url = process.env.EXPO_PUBLIC_TELEMETRY_URL || 'ws://81.71.71.31/ws';

function loadModule(relativePath, requireStub) {
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, relativePath), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require: requireStub });
  return module.exports;
}

const replayDataset = loadModule('../src/data/replay-dataset.ts', () => {
  throw new Error('replay-dataset.ts 不应有运行时依赖');
});
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
    if (name === '@/config') return { TELEMETRY_WS_URL: url };
    if (name === '@/data/replay-dataset') return replayDataset;
    if (name === '@/services/api') return { getToken: () => null };
    throw new Error(`unexpected require: ${name}`);
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

// ─── 告警引擎检查：用真实规则配置跑状态机全周期 ───
const alertsModule = loadModule('../src/constants/alerts.ts', () => {
  throw new Error('constants/alerts.ts 不应有运行时依赖');
});
const { AlertEngine } = loadModule('../src/services/alert-engine.ts', (name) => {
  assert.equal(name, '@/constants/alerts');
  return alertsModule;
});

function makeHealthFrame(overrides = {}, ts = 0, batteryOverrides = {}) {
  return {
    deviceId: 'test',
    tsMs: ts,
    receivedAt: new Date(ts).toISOString(),
    health: {
      valid: true,
      heartRate: 120,
      spo2: 97,
      bodyTemp: 38.6,
      envTemp: 24,
      respiration: 24,
      microCir: 4,
      fatigue: 5,
      hrvSdnn: 40,
      hrvRmssd: 30,
      rrInterval: 500,
      systolic: 115,
      diastolic: 75,
      ...overrides,
    },
    battery: { ok: true, found: true, percent: 80, voltage: 3.9, ...batteryOverrides },
  };
}

{
  const engine = new AlertEngine({ staleAfterMs: null, onChange: () => {} });
  let ts = 0;
  const step = (overrides) => engine.onFrame(makeHealthFrame(overrides, (ts += 2000)), ts);

  // 去抖：4 帧越界不触发，第 5 帧触发
  for (let i = 0; i < 4; i++) step({ heartRate: 190 });
  assert.equal(engine.getActive().length, 0);
  step({ heartRate: 192 });
  const [high] = engine.getActive();
  assert.equal(high.ruleId, 'heartRateHigh');
  assert.equal(high.severity, 'critical');
  assert.ok(high.detail.includes('192'));

  // 滞回带内（165–180）不解除
  for (let i = 0; i < 5; i++) step({ heartRate: 172 });
  assert.equal(engine.getActive().length, 1);

  // 单帧毛刺不误触发
  step({ spo2: 88 });
  step({ spo2: 97 });
  assert.equal(engine.getActive().filter((a) => a.ruleId === 'spo2Low').length, 0);

  // 回稳 3 帧解除
  for (let i = 0; i < 3; i++) step({ heartRate: 160 });
  assert.equal(engine.getActive().length, 0);
}
{
  const engine = new AlertEngine({ staleAfterMs: null, onChange: () => {} });
  let ts = 0;
  const step = (percent) => engine.onFrame(makeHealthFrame({}, (ts += 2000), { percent }), ts);

  step(15);
  step(15);
  assert.equal(engine.getActive().length, 0);
  step(15);
  const [battery] = engine.getActive();
  assert.equal(battery.ruleId, 'batteryLow');
  assert.equal(battery.severity, 'warning');
  step(8);
  assert.equal(engine.getActive()[0].severity, 'critical');
  for (let i = 0; i < 3; i++) step(30);
  assert.equal(engine.getActive().length, 0);
}
{
  const engine = new AlertEngine({ staleAfterMs: null, onChange: () => {} });
  // 传感器离身：89 帧无效不触发，第 90 帧触发；1 帧有效即解除
  for (let i = 0; i < 89; i++)
    engine.onFrame(makeHealthFrame({ valid: false }, i * 2000), i * 2000);
  assert.equal(engine.getActive().length, 0);
  engine.onFrame(makeHealthFrame({ valid: false }, 178000), 178000);
  const [off] = engine.getActive();
  assert.equal(off.ruleId, 'sensorOff');
  assert.equal(off.severity, 'warning');
  engine.acknowledge('sensorOff');
  assert.equal(engine.getActive()[0].acknowledged, true);
  engine.onFrame(makeHealthFrame({}, 180000), 180000);
  assert.equal(engine.getActive().length, 0);
}
{
  const engine = new AlertEngine({ staleAfterMs: 30_000, onChange: () => {} });
  engine.onTick(1000, true);
  assert.equal(engine.getActive().length, 0); // 从未收到过帧：不触发
  engine.onFrame(makeHealthFrame({}, 1000), 1000);
  engine.onTick(20_000, true);
  assert.equal(engine.getActive().length, 0); // 19s < 30s
  engine.onTick(32_000, true);
  const [stale] = engine.getActive();
  assert.equal(stale.ruleId, 'dataStale');
  assert.equal(stale.severity, 'info');
  engine.onTick(33_000, false); // 云端断开：交给连接状态提示
  assert.equal(engine.getActive().length, 0);
}
console.log('Alert engine checks passed');

// ─── UWB 解析与三边定位检查 ───
const uwbMapModule = loadModule('../src/constants/uwb-map.ts', () => {
  throw new Error('uwb-map.ts 不应有运行时依赖');
});
{
  const anchors = uwbMapModule.FLOOR_ANCHORS;
  const target = { x: 450, y: 400 };
  const distanceMm = anchors.map((anchor) =>
    Math.round(Math.hypot(target.x - anchor.x, target.y - anchor.y) * 10),
  );
  const uwbPacket = {
    topic: 'petsense/sensor/esp32-001',
    received_at: new Date().toISOString(),
    payload: {
      deviceId: 'test',
      ts_ms: 100,
      uwb: {
        valid: true,
        receivedMs: 1,
        timer: 2,
        tagId: 3,
        anchorId: 0,
        sequence: 4,
        anchorMask: 0b1111,
        kalmanEnabled: true,
        distanceMm,
        rawMm: distanceMm,
        kalmanMm: distanceMm,
      },
    },
  };
  const uwbFrame = parseTelemetryMessage(JSON.stringify(uwbPacket));
  assert.ok(uwbFrame?.uwb?.valid);
  assert.equal(uwbFrame.uwb.distanceMm.length, uwbMapModule.FLOOR_ANCHORS.length);
  assert.equal(
    parseTelemetryMessage(
      JSON.stringify({
        ...uwbPacket,
        payload: { ...uwbPacket.payload, uwb: { valid: true, tagId: 'x' } },
      }),
    ),
    null,
  );

  const uwbPosition = loadModule('../src/services/uwb-position.ts', (name) => {
    assert.equal(name, '@/constants/uwb-map');
    return uwbMapModule;
  });
  const readings = anchors.map((anchor) => ({
    x: anchor.x,
    y: anchor.y,
    distance: Math.hypot(target.x - anchor.x, target.y - anchor.y),
  }));
  const solved = uwbPosition.trilaterate(readings);
  assert.ok(solved, 'trilaterate should solve');
  assert.ok(
    Math.hypot(solved.x - target.x, solved.y - target.y) < 1,
    `solve error: ${JSON.stringify(solved)}`,
  );
  assert.equal(uwbPosition.trilaterate(readings.slice(0, 2)), null, '少于3锚点应返回 null');
  const solvedFromFrame = uwbPosition.positionFromUwb(uwbFrame.uwb);
  assert.ok(
    solvedFromFrame && Math.hypot(solvedFromFrame.x - target.x, solvedFromFrame.y - target.y) < 1,
    `positionFromUwb should recover target: ${JSON.stringify(solvedFromFrame)}`,
  );
  assert.equal(uwbPosition.positionFromUwb({ ...uwbFrame.uwb, valid: false }), null);
}
console.log('UWB checks passed');

// ─── 跨端上传检查（Web：fetch+Blob 组表；原生：XHR{uri} multipart） ───
{
  // form-upload 需要浏览器式全局（FormData/Blob/fetch），vm 沙箱单独注入
  function loadFormUpload(withDocument, extraGlobals = {}) {
    const code = ts.transpileModule(
      fs.readFileSync(path.join(__dirname, '../src/services/form-upload.ts'), 'utf8'),
      { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
    ).outputText;
    const module = { exports: {} };
    const sandbox = {
      module,
      exports: module.exports,
      require: () => {
        throw new Error('form-upload.ts 不应有运行时依赖');
      },
      document: withDocument ? {} : undefined,
      FormData,
      Blob,
      fetch,
      ...extraGlobals,
    };
    vm.runInNewContext(code, sandbox);
    return module.exports;
  }

  const dataUri =
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

  (async () => {
    // Web 分支（注入 document）：fetch(data:) → Blob → append 不抛 Unsupported FormDataPart
    const webMod = loadFormUpload(true);
    const webForm = await webMod.buildUploadForm({ caption: '测试', media_kind: 'gif' }, [
      { field: 'image', uri: dataUri, name: 'dot.png', type: 'image/png' },
    ]);
    assert.ok(webForm instanceof FormData, 'web 分支应返回 FormData');
    const webParts = [];
    for (const [key, value] of webForm.entries()) {
      webParts.push([key, value instanceof Blob ? 'blob' : value]);
    }
    assert.deepEqual(
      webParts.map((p) => p[0]),
      ['caption', 'media_kind', 'image'],
      'web 分支字段顺序',
    );
    assert.equal(
      webParts[2][1],
      'blob',
      'web 分支文件必须是 Blob（否则 react-native-web 抛 Unsupported FormDataPart）',
    );

    // 原生分支（无 document）：buildUploadForm 必须拒绝——expo/fetch 无法序列化 {uri} part，
    // 原生端唯一正确通道是 sendMultipart（XHR + RN FormData）
    const nativeMod = loadFormUpload(false);
    await assert.rejects(
      () =>
        nativeMod.buildUploadForm({ name: 'x' }, [
          { field: 'photo', uri: 'file:///tmp/a.jpg', name: 'a.jpg', type: 'image/jpeg' },
        ]),
      /sendMultipart/,
      '原生端 buildUploadForm 应拒绝并指向 sendMultipart',
    );

    // 原生 sendMultipart：注入假 XMLHttpRequest 与 RN 语义的 FormData（append 保留原始值，
    // 不像 Node FormData 把对象字符串化），断言 {uri,name,type} part、请求头与响应回传
    let captured = null;
    class FakeFormData {
      constructor() {
        this.parts = [];
      }
      append(key, value) {
        this.parts.push([key, value]);
      }
      *entries() {
        yield* this.parts;
      }
    }
    class FakeXHR {
      open(method, url) {
        captured.method = method;
        captured.url = url;
      }
      setRequestHeader(k, v) {
        captured.headers[k] = v;
      }
      send(form) {
        captured.parts = [...form.entries()];
        this.status = 201;
        this.responseText = '{"success":true}';
        setTimeout(() => this.onload(), 0);
      }
    }
    const nativeSandbox = {
      XMLHttpRequest: FakeXHR,
      FormData: FakeFormData,
    };
    const xhrMod = loadFormUpload(false, nativeSandbox);
    captured = { headers: {} };
    const response = await xhrMod.sendMultipart('http://localhost:8080/api/app/pets/1', {
      method: 'PATCH',
      headers: { 'X-Petsense-Session': 'tok' },
      fields: { name: '豆豆' },
      files: [{ field: 'photo', uri: 'file:///tmp/a.jpg', name: 'a.jpg', type: 'image/jpeg' }],
    });
    assert.equal(captured.method, 'PATCH');
    assert.equal(captured.url, 'http://localhost:8080/api/app/pets/1');
    assert.equal(captured.headers['X-Petsense-Session'], 'tok');
    assert.deepEqual(
      captured.parts.map((p) => p[0]),
      ['name', 'photo'],
      '原生分支字段顺序',
    );
    assert.deepEqual(
      JSON.parse(JSON.stringify(captured.parts[1][1])),
      { uri: 'file:///tmp/a.jpg', name: 'a.jpg', type: 'image/jpeg' },
      '原生分支保留 {uri,name,type} 对象 part（由 RCTNetworking 原生读取文件）',
    );
    assert.deepEqual(JSON.parse(JSON.stringify(response)), {
      status: 201,
      body: '{"success":true}',
    });
    delete global.document;
    console.log('Form upload checks passed');
  })().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

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
