/**
 * Agent WS 直连客户端（W01，模块级单例）：
 * - 连接 <API 根>/api/app/agent/ws?token=…&protocol=group-v1（EXPO_PUBLIC_AGENT_WS_URL 可覆盖）；
 * - 服务端推送 {type:'turn'} 整回合快照与 {type:'delta'} 流式增量（累计全文），
 *   另有 30s 心跳 {type:'ping'} 与未登录 {type:'unauthorized'}；
 * - 双 Agent 群聊（docs/agent-dual-agent-chat-design.md §4.3）：连接声明 group-v1 后，账号开放时
 *   流式增量按消息推送（message_delta / message / message_status），turn 快照带逐条回复；
 *   账号未开放时声明不生效，照旧收上面的旧形状帧；
 * - 指数退避自动重连；重连后由调用方 GET turns 拉齐（以服务端已保存结果为准）；
 * - 断线期间调用方降级轮询（agent-w01-spec.md §3.6）；
 * - 多会话（docs/agent-w16-spec.md §5.5）：每种帧都带 conversationId，客户端据此把帧送进对应的会话；
 *   后台交付与增删改之后另推 {type:'conversation'} 会话摘要。
 */

import { API_BASE_URL } from '@/config';
import { getToken } from '@/services/api';
import {
  AGENT_GROUP_PROTOCOL,
  type AgentConversation,
  type AgentReplyMessage,
  type AgentTurn,
} from '@/services/agent';

const MAX_RETRY_DELAY_MS = 15_000;
/** 服务端心跳 30s；连续 75s 无任何下行视为半死连接，主动断开触发重连 */
const HEARTBEAT_SILENCE_MS = 75_000;
const WATCHDOG_TICK_MS = 5_000;

export type AgentChannelStatus = 'connecting' | 'open' | 'closed' | 'unauthorized';

/** 回合快照；conversation 是随行的会话摘要（回合落定、列表要跟着变时才有，否则为空） */
export type AgentTurnFrame = {
  type: 'turn';
  petId: number;
  conversationId: number | null;
  turn: AgentTurn;
  conversation?: AgentConversation | null;
};
/** 会话摘要（后台交付、新建、改名、删除之后）：deletedAt 非空表示这个会话已删除 */
export type AgentConversationFrame = {
  type: 'conversation';
  conversation: AgentConversation;
};
export type AgentDeltaFrame = {
  type: 'delta';
  petId: number;
  conversationId: number | null;
  turnId: number;
  text: string;
  speaker: AgentTurn['speaker'];
};
/** 群聊消息的流式增量：text 是这条消息的累计正文，version 是它所属的持久化版本 */
export type AgentMessageDeltaFrame = {
  type: 'message_delta';
  petId: number;
  conversationId: number | null;
  turnId: number;
  messageId: string;
  role: AgentReplyMessage['role'];
  text: string;
  version: number;
};
/** 群聊消息快照（领取、终态、卡片同步） */
export type AgentMessageFrame = {
  type: 'message';
  petId: number;
  conversationId: number | null;
  turnId: number;
  message: AgentReplyMessage;
};
/** 群聊消息的瞬时状态（如联网核实中）：不是正文 */
export type AgentMessageStatusFrame = {
  type: 'message_status';
  petId: number;
  conversationId: number | null;
  turnId: number;
  messageId: string;
  role: AgentReplyMessage['role'];
  status: 'searching';
};
export type AgentFrame =
  | AgentTurnFrame
  | AgentConversationFrame
  | AgentDeltaFrame
  | AgentMessageDeltaFrame
  | AgentMessageFrame
  | AgentMessageStatusFrame
  | { type: 'ping'; ts?: number };

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
    const ws = new WebSocket(
      `${agentWsUrl()}?token=${encodeURIComponent(token)}&protocol=${AGENT_GROUP_PROTOCOL}`,
    );
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
