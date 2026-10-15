/**
 * 遥测客户端（模块级单例），按 TELEMETRY_MODE 提供两种数据源：
 * - live：连接云端 WebSocket，服务端在收到 MQTT 传感器帧后向 /ws 广播
 *   {topic, payload, received_at}，解析、过滤非传感器帧后分发给订阅者；
 *   服务端每 10s 发一帧 {type:"ping"} 心跳，配合看门狗检测半死连接；
 * - replay：不联网，按设备原始节奏循环回放 src/data/replay-dataset.ts 的实验数据集片段。
 */

import { TELEMETRY_MODE, TELEMETRY_WS_URL } from '@/config';
import { REPLAY_BATTERY, REPLAY_HEALTH, REPLAY_META } from '@/data/replay-dataset';
import type { TelemetryFrame, TelemetryPacket, TelemetryStatus } from '@/types/telemetry';

const SENSOR_TOPIC_PREFIX = 'petsense/sensor/';
const MAX_RETRY_DELAY_MS = 15_000;
/** 服务端心跳间隔 10s；连续 25s（含心跳在内）没有任何下行消息视为半死连接 */
const HEARTBEAT_SILENCE_MS = 25_000;
const WATCHDOG_TICK_MS = 5_000;

type FrameListener = (frame: TelemetryFrame) => void;
type StatusListener = (status: TelemetryStatus) => void;

class TelemetryService {
  private ws: WebSocket | null = null;
  private frameListeners = new Set<FrameListener>();
  private statusListeners = new Set<StatusListener>();
  private retryCount = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private status: TelemetryStatus = 'closed';
  private replayTimer: ReturnType<typeof setInterval> | null = null;
  private replayIndex = 0;
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;

  getStatus() {
    return this.status;
  }

  subscribeFrame(listener: FrameListener) {
    this.frameListeners.add(listener);
    this.ensureConnected();
    return () => {
      this.frameListeners.delete(listener);
      if (this.frameListeners.size === 0) {
        this.stopReplay();
        this.stopWatchdog();
        if (this.retryTimer) clearTimeout(this.retryTimer);
        this.retryTimer = null;
        const ws = this.ws;
        this.ws = null;
        if (ws) {
          ws.onclose = null;
          ws.onmessage = null;
          ws.onopen = null;
          ws.onerror = null;
          ws.close();
        }
        this.retryCount = 0;
        this.setStatus('closed');
      }
    };
  }

  subscribeStatus(listener: StatusListener) {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => this.statusListeners.delete(listener);
  }

  private setStatus(status: TelemetryStatus) {
    if (this.status === status) return;
    this.status = status;
    this.statusListeners.forEach((listener) => listener(status));
  }

  private ensureConnected() {
    if (TELEMETRY_MODE === 'replay') {
      this.ensureReplay();
      return;
    }
    if (this.ws || this.retryTimer) return;
    this.connect();
  }

  private ensureReplay() {
    if (this.replayTimer) return;
    this.setStatus('replaying');
    this.emitReplayFrame();
    this.replayTimer = setInterval(() => this.emitReplayFrame(), REPLAY_META.cadenceMs);
  }

  private stopReplay() {
    if (this.replayTimer) {
      clearInterval(this.replayTimer);
      this.replayTimer = null;
    }
  }

  private emitReplayFrame() {
    const t = REPLAY_HEALTH[this.replayIndex % REPLAY_HEALTH.length];
    this.replayIndex += 1;
    const batteryTuple = REPLAY_BATTERY.filter((b) => b[0] <= t[0]).at(-1);
    const frame: TelemetryFrame = {
      deviceId: REPLAY_META.deviceId,
      tsMs: t[0],
      receivedAt: new Date().toISOString(),
      health: {
        valid: t[1] === 1,
        heartRate: t[2],
        spo2: t[3],
        bodyTemp: t[4],
        envTemp: t[5],
        respiration: t[6],
        microCir: t[7],
        fatigue: t[8],
        rrInterval: t[9],
        hrvSdnn: t[10],
        hrvRmssd: t[11],
        systolic: t[12],
        diastolic: t[13],
      },
      battery: batteryTuple
        ? { ok: true, found: true, percent: batteryTuple[1], voltage: batteryTuple[2] }
        : undefined,
    };
    this.frameListeners.forEach((listener) => listener(frame));
  }

  private connect() {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.setStatus(this.retryCount > 0 ? 'reconnecting' : 'connecting');
    this.lastMessageAt = Date.now();
    this.startWatchdog();

    const ws = new WebSocket(TELEMETRY_WS_URL);
    this.ws = ws;

    ws.onopen = () => {
      this.retryCount = 0;
      this.lastMessageAt = Date.now();
      this.setStatus('open');
    };
    ws.onmessage = (event) => {
      this.lastMessageAt = Date.now();
      this.handleMessage(event.data);
    };
    ws.onclose = () => this.handleDisconnect();
    ws.onerror = () => {
      // onclose 随后触发，统一在 handleDisconnect 里重连
    };
  }

  private handleDisconnect() {
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws = null;
    }
    this.stopWatchdog();
    if (this.frameListeners.size === 0) {
      this.retryCount = 0;
      this.setStatus('closed');
      return;
    }
    const delay = Math.min(1000 * 2 ** this.retryCount, MAX_RETRY_DELAY_MS);
    this.retryCount += 1;
    this.setStatus('reconnecting');
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  /** 半死连接检测：open 状态下长时间（含心跳在内）无任何下行消息，主动断开走统一重连 */
  private startWatchdog() {
    if (this.watchdogTimer) return;
    this.watchdogTimer = setInterval(() => {
      if (!this.ws || this.status !== 'open') return;
      if (Date.now() - this.lastMessageAt <= HEARTBEAT_SILENCE_MS) return;
      const ws = this.ws;
      this.ws = null;
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
      try {
        ws.close();
      } catch {
        // close 失败也不影响，状态已按断开处理
      }
      this.handleDisconnect();
    }, WATCHDOG_TICK_MS);
  }

  private stopWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  private handleMessage(raw: unknown) {
    const frame = parseTelemetryMessage(raw);
    if (frame) this.frameListeners.forEach((listener) => listener(frame));
  }
}

export function parseTelemetryMessage(raw: unknown): TelemetryFrame | null {
  if (typeof raw !== 'string') return null;
  let packet: TelemetryPacket;
  try {
    packet = JSON.parse(raw) as TelemetryPacket;
  } catch {
    return null;
  }
  if (typeof packet?.topic !== 'string' || !packet.topic.startsWith(SENSOR_TOPIC_PREFIX))
    return null;
  if (typeof packet.received_at !== 'string' || !Number.isFinite(Date.parse(packet.received_at)))
    return null;

  let payload: Record<string, unknown> | null = null;
  if (typeof packet.payload === 'string') {
    const parsed = safeParse(packet.payload);
    payload = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } else if (packet.payload && typeof packet.payload === 'object') {
    payload = packet.payload as Record<string, unknown>;
  }
  if (!payload || Array.isArray(payload)) return null;

  // 音频分片 / 重组进度帧没有 health 结构，APP 端不消费
  if (!payload.health && !payload.battery && !payload.imu && !payload.audio && !payload.uwb)
    return null;
  if (typeof payload.deviceId !== 'string' || !Number.isFinite(payload.ts_ms)) return null;

  const frame: TelemetryFrame = {
    deviceId: typeof payload.deviceId === 'string' ? payload.deviceId : 'unknown',
    tsMs: typeof payload.ts_ms === 'number' ? payload.ts_ms : 0,
    receivedAt: packet.received_at,
    health:
      numericObject(payload.health, [
        'heartRate',
        'spo2',
        'bodyTemp',
        'envTemp',
        'respiration',
        'microCir',
        'fatigue',
        'hrvSdnn',
        'hrvRmssd',
        'rrInterval',
        'systolic',
        'diastolic',
      ]) && typeof (payload.health as Record<string, unknown>).valid === 'boolean'
        ? (payload.health as TelemetryFrame['health'])
        : undefined,
    battery:
      numericObject(payload.battery, ['percent', 'voltage']) &&
      typeof (payload.battery as Record<string, unknown>).found === 'boolean'
        ? (payload.battery as TelemetryFrame['battery'])
        : undefined,
    imu: vectorObject(payload.imu, { accel: 3, gyro: 3, mag: 3, euler: 3, quat: 4, baro: 4 })
      ? (payload.imu as TelemetryFrame['imu'])
      : undefined,
    audio:
      numericObject(payload.audio, ['sampleRate', 'bits', 'frame', 'rms', 'zcr']) &&
      vectorObject(payload.audio, { mel: 40 })
        ? (payload.audio as TelemetryFrame['audio'])
        : undefined,
    experiment: validExperiment(payload.experiment)
      ? (payload.experiment as TelemetryFrame['experiment'])
      : undefined,
    uwb: validUwb(payload.uwb) ? (payload.uwb as TelemetryFrame['uwb']) : undefined,
  };
  return frame.health || frame.battery || frame.imu || frame.audio || frame.uwb ? frame : null;
}

function validUwb(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const object = value as Record<string, unknown>;
  return (
    typeof object.valid === 'boolean' &&
    ['tagId', 'anchorId', 'sequence', 'anchorMask'].every(
      (key) => typeof object[key] === 'number' && Number.isFinite(object[key]),
    ) &&
    typeof object.kalmanEnabled === 'boolean' &&
    ['distanceMm', 'rawMm', 'kalmanMm'].every(
      (key) =>
        Array.isArray(object[key]) &&
        (object[key] as unknown[]).every(
          (item) => typeof item === 'number' && Number.isFinite(item),
        ),
    )
  );
}

function numericObject(value: unknown, keys: string[]): boolean {
  return (
    !!value &&
    typeof value === 'object' &&
    keys.every(
      (key) =>
        typeof (value as Record<string, unknown>)[key] === 'number' &&
        Number.isFinite((value as Record<string, unknown>)[key]),
    )
  );
}

function vectorObject(value: unknown, lengths: Record<string, number>): boolean {
  if (!value || typeof value !== 'object') return false;
  const object = value as Record<string, unknown>;
  return (
    typeof object.ok === 'boolean' &&
    Object.entries(lengths).every(([key, length]) => {
      const vector = object[key];
      return (
        Array.isArray(vector) &&
        vector.length === length &&
        vector.every((item) => typeof item === 'number' && Number.isFinite(item))
      );
    })
  );
}

function validExperiment(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const object = value as Record<string, unknown>;
  return (
    ['active', 'eventActive', 'sdLogging'].every((key) => typeof object[key] === 'boolean') &&
    ['id', 'eventId', 'eventLabel', 'eventSegment', 'sdStatus', 'sdAudioMode'].every(
      (key) => typeof object[key] === 'string',
    ) &&
    numericObject(value, ['sdRows'])
  );
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export const telemetryService = new TelemetryService();
