/**
 * 遥测客户端（模块级单例），按 TELEMETRY_MODE 提供两种数据源：
 * - live：连接云端 WebSocket，服务端在收到 MQTT 传感器帧后向 /ws 广播
 *   {topic, payload, received_at}，这里解析、过滤非传感器帧后分发给订阅者；
 * - replay：不联网，按设备原始节奏循环回放 src/data/replay-dataset.ts 的实验数据集片段。
 */

import { TELEMETRY_MODE, TELEMETRY_WS_URL } from '@/config';
import { REPLAY_BATTERY, REPLAY_HEALTH, REPLAY_META } from '@/data/replay-dataset';
import type { TelemetryFrame, TelemetryPacket, TelemetryStatus } from '@/types/telemetry';

const SENSOR_TOPIC_PREFIX = 'petsense/sensor/';
const MAX_RETRY_DELAY_MS = 15_000;

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

  getStatus() {
    return this.status;
  }

  subscribeFrame(listener: FrameListener) {
    this.frameListeners.add(listener);
    this.ensureConnected();
    return () => {
      this.frameListeners.delete(listener);
      // 回放模式没有断连回调可依托，订阅清空时在此收尾
      if (TELEMETRY_MODE === 'replay' && this.frameListeners.size === 0) {
        this.stopReplay();
        this.setStatus('closed');
      }
    };
  }

  subscribeStatus(listener: StatusListener) {
    this.statusListeners.add(listener);
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
    if (this.ws && (this.status === 'open' || this.status === 'connecting')) return;
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

    const ws = new WebSocket(TELEMETRY_WS_URL);
    this.ws = ws;

    ws.onopen = () => {
      this.retryCount = 0;
      this.setStatus('open');
    };
    ws.onmessage = (event) => this.handleMessage(event.data);
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

  private handleMessage(raw: unknown) {
    if (typeof raw !== 'string') return;
    let packet: TelemetryPacket;
    try {
      packet = JSON.parse(raw) as TelemetryPacket;
    } catch {
      return;
    }
    if (!packet?.topic?.startsWith(SENSOR_TOPIC_PREFIX)) return;

    let payload: Record<string, unknown> | null = null;
    if (typeof packet.payload === 'string') {
      const parsed = safeParse(packet.payload);
      payload = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
    } else if (packet.payload && typeof packet.payload === 'object') {
      payload = packet.payload as Record<string, unknown>;
    }
    if (!payload) return;

    // 音频分片 / 重组进度帧没有 health 结构，APP 端不消费
    if (!payload.health && !payload.battery) return;

    const frame: TelemetryFrame = {
      deviceId: typeof payload.deviceId === 'string' ? payload.deviceId : 'unknown',
      tsMs: typeof payload.ts_ms === 'number' ? payload.ts_ms : 0,
      receivedAt: packet.received_at,
      health: payload.health as TelemetryFrame['health'],
      battery: payload.battery as TelemetryFrame['battery'],
    };
    this.frameListeners.forEach((listener) => listener(frame));
  }
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export const telemetryService = new TelemetryService();
