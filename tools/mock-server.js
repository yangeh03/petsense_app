#!/usr/bin/env node
/**
 * PetSense 本地模拟服务（零依赖）。
 * 模拟线上 server.py 的 /ws 行为：每秒广播一帧传感器数据，
 * APP 通过 EXPO_PUBLIC_TELEMETRY_URL=ws://<本机IP>:8090/ws 连接本地联调。
 *
 * 用法：node tools/mock-server.js [端口，默认 8090]
 */

const crypto = require('crypto');
const http = require('http');

const PORT = Number(process.argv[2] || 8090);
const TOPIC = 'petsense/sensor/esp32-001';

// ─── 模拟生理数据（随机游走） ───
const state = { heartRate: 112, spo2: 97.5, bodyTemp: 38.6, respiration: 24, battery: 82.4 };

function walk(key, min, max, step) {
  state[key] = Math.min(max, Math.max(min, state[key] + (Math.random() - 0.5) * 2 * step));
}

function makeFrame() {
  walk('heartRate', 88, 132, 3);
  walk('spo2', 94, 99.5, 0.4);
  walk('bodyTemp', 38.1, 39.1, 0.05);
  walk('respiration', 18, 30, 1);
  walk('battery', 5, 100, 0.01);

  const payload = {
    deviceId: 'esp32-001',
    ts_ms: Date.now(),
    health: {
      valid: true,
      heartRate: Math.round(state.heartRate),
      spo2: Math.round(state.spo2),
      bodyTemp: Number(state.bodyTemp.toFixed(2)),
      envTemp: Number((state.bodyTemp - 4).toFixed(2)),
      respiration: Math.round(state.respiration),
      microCir: Math.round(3 + Math.random() * 2),
      fatigue: Math.round(Math.random() * 20),
      hrvSdnn: Math.round(28 + Math.random() * 18),
      hrvRmssd: Math.round(22 + Math.random() * 15),
      rrInterval: Math.round(60000 / state.heartRate),
      systolic: Math.round(110 + Math.random() * 12),
      diastolic: Math.round(68 + Math.random() * 10),
    },
    battery: {
      ok: true,
      found: true,
      percent: Number(state.battery.toFixed(1)),
      voltage: Number((3.7 + Math.random() * 0.2).toFixed(3)),
    },
  };

  return JSON.stringify({
    topic: TOPIC,
    payload: JSON.stringify(payload),
    received_at: new Date().toISOString(),
  });
}

// ─── 极简 WebSocket 服务端（仅握手 + 下行文本帧广播） ───
const clients = new Set();

function encodeTextFrame(text) {
  const payload = Buffer.from(text);
  const length = payload.length;
  let header;
  if (length < 126) {
    header = Buffer.from([0x81, length]);
  } else if (length < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(length, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(length), 2);
  }
  return Buffer.concat([header, payload]);
}

const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'Access-Control-Allow-Origin': '*' });
    res.end(JSON.stringify({ clients: clients.size, topic: TOPIC }));
    return;
  }
  res.writeHead(404);
  res.end();
});

server.on('upgrade', (req, socket) => {
  const key = req.headers['sec-websocket-key'];
  if (!key || !(req.url || '').startsWith('/ws')) {
    socket.destroy();
    return;
  }
  const accept = crypto
    .createHash('sha1')
    .update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
    .digest('base64');
  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\n' +
      'Upgrade: websocket\r\n' +
      'Connection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
  );
  clients.add(socket);
  console.log(`[mock] 客户端接入，当前 ${clients.size} 个`);

  socket.on('close', () => {
    clients.delete(socket);
    console.log(`[mock] 客户端断开，当前 ${clients.size} 个`);
  });
  socket.on('error', () => {
    clients.delete(socket);
  });
  // 忽略上行帧（收到 close 帧直接关闭）
  socket.on('data', (buf) => {
    if (buf.length > 0 && (buf[0] & 0x0f) === 0x08) socket.end();
  });
});

setInterval(() => {
  if (clients.size === 0) return;
  const frame = encodeTextFrame(makeFrame());
  for (const socket of clients) {
    try {
      socket.write(frame);
    } catch {
      clients.delete(socket);
    }
  }
}, 1000);

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[mock] PetSense 模拟服务已启动 ws://0.0.0.0:${PORT}/ws（1s/帧）`);
});
