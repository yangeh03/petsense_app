/**
 * Agent WS 直连客户端（W01，模块级单例）：
 * - 连接 <API 根>/api/app/agent/ws?token=…（EXPO_PUBLIC_AGENT_WS_URL 可覆盖）；
 * - 服务端推送 {type:'turn'} 整回合快照与 {type:'delta'} 流式增量（累计全文），
 *   另有 30s 心跳 {type:'ping'} 与未登录 {type:'unauthorized'}；
 * - 指数退避自动重连；重连后由调用方 GET turns 拉齐（以服务端已保存结果为准）；
 * - 断线期间调用方降级轮询（agent-w01-spec.md §3.6）。
 */

import { API_BASE_URL } from '@/config';
import { getToken } from '@/services/api';
import type { AgentTurn } from '@/services/agent';

const MAX_RETRY_DELAY_MS = 15_000;
/** 服务端心跳 30s；连续 75s 无任何下行视为半死连接，主动断开触发重连 */
const HEARTBEAT_SILENCE_MS = 75_000;
const WATCHDOG_TICK_MS = 5_000;

export type AgentChannelStatus = 'connecting' | 'open' | 'closed' | 'unauthorized';

export type AgentTurnFrame = { type: 'turn'; petId: number; turn: AgentTurn };
export type AgentDeltaFrame = {
  type: 'delta';
  petId: number;
  turnId: number;
  text: string;
  speaker: AgentTurn['speaker'];
};
export type AgentFrame = AgentTurnFrame | AgentDeltaFrame | { type: 'ping'; ts?: number };

type FrameListener = (frame: AgentFrame) => void;
type StatusListener = (status: AgentChannelStatus) => void;

function agentWsUrl() {
  const override = process.env.EXPO_PUBLIC_AGENT_WS_URL;
  if (override) return override;
  return `${API_BASE_URL.replace(/^http/, 'ws')}/api/app/agent/ws`;
}

class AgentChannel {
  private ws: WebSocket | null = null;
  private frameListeners = new Set<FrameListener>();
  private statusListeners = new Set<StatusListener>();
  private retryCount = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private status: AgentChannelStatus = 'closed';
  private rejectedAuth = false;

  getStatus() {
    return this.status;
  }

  /** 登录态变化后由页面/鉴权层调用：带上新 token 立即重连 */
  reconnect() {
    this.rejectedAuth = false;
    this.retryCount = 0;
    this.teardownSocket();
    if (this.frameListeners.size > 0) {
      this.connect();
    } else {
      this.setStatus('closed');
    }
  }

  subscribe(listener: FrameListener) {
    this.frameListeners.add(listener);
    this.ensureConnected();
    return () => {
      this.frameListeners.delete(listener);
      if (this.frameListeners.size === 0) {
        this.teardownSocket();
        if (this.retryTimer) {
          clearTimeout(this.retryTimer);
          this.retryTimer = null;
        }
        this.retryCount = 0;
        this.stopWatchdog();
        this.setStatus('closed');
      }
    };
  }

  subscribeStatus(listener: StatusListener) {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => this.statusListeners.delete(listener);
  }

  private setStatus(status: AgentChannelStatus) {
    if (this.status === status) return;
    this.status = status;
    this.statusListeners.forEach((listener) => listener(status));
  }

  private teardownSocket() {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.onopen = null;
      ws.onerror = null;
      try {
        ws.close();
      } catch {
        // 已断开也无妨
      }
    }
  }

  private ensureConnected() {
    if (this.ws || this.retryTimer || this.rejectedAuth) return;
    this.connect();
  }

  private connect() {
    const token = getToken();
    if (!token) {
      this.setStatus('closed');
      return;
    }
    this.setStatus('connecting');
    const ws = new WebSocket(`${agentWsUrl()}?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    this.lastMessageAt = Date.now();
    ws.onopen = () => {
      this.retryCount = 0;
      this.startWatchdog();
      this.setStatus('open');
    };
    ws.onmessage = (event) => {
      this.lastMessageAt = Date.now();
      this.handleRaw(String(event.data));
    };
    ws.onclose = () => {
      this.stopWatchdog();
      if (this.ws === ws) {
        this.ws = null;
        this.scheduleRetry();
      }
    };
    ws.onerror = () => {
      // onclose 随后触发，统一走重连
    };
  }

  private scheduleRetry() {
    if (this.rejectedAuth || this.frameListeners.size === 0) {
      this.setStatus('closed');
      return;
    }
    const delay = Math.min(1_000 * 2 ** this.retryCount, MAX_RETRY_DELAY_MS);
    this.retryCount += 1;
    this.setStatus('connecting');
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, delay);
  }

  private handleUnauthorized() {
    this.rejectedAuth = true;
    this.setStatus('unauthorized');
    this.teardownSocket();
  }

  private startWatchdog() {
    if (this.watchdogTimer) return;
    this.watchdogTimer = setInterval(() => {
      if (Date.now() - this.lastMessageAt < HEARTBEAT_SILENCE_MS) return;
      const ws = this.ws;
      if (!ws) return;
      // 半死连接：强制断开走正常重连
      ws.onclose = null;
      this.teardownSocket();
      this.scheduleRetry();
    }, WATCHDOG_TICK_MS);
  }

  private stopWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  /** 仅供测试：把收到的原始消息当作帧解析（unauthorized 语义在内） */
  handleRaw(raw: string) {
    let frame: AgentFrame | { type: 'unauthorized' } | null = null;
    try {
      frame = JSON.parse(raw) as AgentFrame | { type: 'unauthorized' };
    } catch {
      return;
    }
    if (!frame) return;
    if (frame.type === 'unauthorized') {
      this.handleUnauthorized();
      return;
    }
    if (frame.type !== 'ping') {
      this.frameListeners.forEach((listener) => listener(frame));
    }
  }
}

export const agentChannel = new AgentChannel();
