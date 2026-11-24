/**
 * 伙伴会话回合列表的纯逻辑（乐观 UI）。
 *
 * 缺陷背景：服务端 POST /agent/pets/<id>/turns 会阻塞到整轮生成结束才返回
 * （agent-w01-spec.md §3.2），客户端若只在响应回来后才落自己的那条消息，
 * 主人的话就要等整轮生成结束才出现在会话里（W01 spec §4.4「发送走乐观 UI」
 * 与 V11 §18.3 的约定此前没有落到实现）。
 *
 * 这里把「本地乐观回合」的插入、与服务端回合的对账、列表排序、流式增量归属
 * 抽成纯函数：store 与 hook 只做状态搬运，行为可单测覆盖。
 */

import type { AgentReplyMessage, AgentTurn } from '@/services/agent';

/**
 * 本地乐观回合：主人刚发出、服务端尚未确认的那一条。
 * 服务端回合 id 为正（SQLite 自增），本地用负 id 并带 local 标记，两者不会撞号。
 */
export type LocalAgentTurn = AgentTurn & { local?: true };

export function isLocalTurn(turn: AgentTurn): boolean {
  return (turn as LocalAgentTurn).local === true;
}

let localSeq = 0;

/** 本地回合 id：-1、-2…（越新越负）。服务端 id 为正，不会撞号。 */
export function nextLocalTurnId(): number {
  localSeq += 1;
  return -localSeq;
}

/**
 * 列表顺序：服务端回合按 id 正序（创建顺序），本地乐观回合排在最后
 * （它一定是刚发出的那一条）。多个本地回合按时间正序（id 递减即更新）。
 */
export function compareTurns(a: AgentTurn, b: AgentTurn): number {
  const localA = isLocalTurn(a);
  const localB = isLocalTurn(b);
  if (localA !== localB) return localA ? 1 : -1;
  if (localA) return b.id - a.id;
  return a.id - b.id;
}

export function orderTurns(turns: AgentTurn[]): AgentTurn[] {
  return [...turns].sort(compareTurns);
}

/**
 * 服务端回合到达时的对账：同 id 覆盖；同 requestId 的本地乐观回合被服务端
 * 权威结果替换（POST 响应与 WS turn 帧先到者先用，结果一致）。
 */
export function reconcileTurn(turns: AgentTurn[], incoming: AgentTurn): AgentTurn[] {
  const kept = incoming.requestId
    ? turns.filter((turn) => !(isLocalTurn(turn) && turn.requestId === incoming.requestId))
    : turns.slice();
  const index = kept.findIndex((turn) => turn.id === incoming.id);
  if (index === -1) return dedupeTurns(orderTurns([...kept, incoming]));
  const next = kept.slice();
  next[index] = mergeTurn(kept[index], incoming);
  return dedupeTurns(orderTurns(next));
}

/**
 * 回合列表去重：同一 id 或同一 requestId 只保留一条。
 *
 * 服务端对 (pet_id, request_id) 有唯一约束，正常不会同时存在两条；但客户端的本地
 * 快照是**上一版客户端**写下的，可能带着旧格式的 requestId 或重复条目，快照恢复与
 * 历史合并都直接把它塞进列表。列表渲染键取 `requestId || id`，一旦出现两条同键
 * 条目，React 会丢弃或复用错位的气泡（并刷「Encountered two children with the same
 * key」告警）。这里按「服务端回合优先、本地乐观回合被替换、同类取较新」收敛。
 */
export function dedupeTurns(turns: AgentTurn[]): AgentTurn[] {
  const picked = new Map<string, AgentTurn>();
  const merged: AgentTurn[] = [];
  for (const turn of turns) {
    const key = turn.requestId || `id:${turn.id}`;
    const previous = picked.get(key);
    if (!previous) {
      picked.set(key, turn);
      merged.push(turn);
      continue;
    }
    const next = pickTurn(previous, turn);
    picked.set(key, next);
    merged[merged.indexOf(previous)] = next;
  }
  // requestId 不同但 id 相同（老快照缺 requestId）也要收敛
  const byId = new Map<number, number>();
  const result: AgentTurn[] = [];
  for (const turn of merged) {
    const at = byId.get(turn.id);
    if (at === undefined) {
      byId.set(turn.id, result.length);
      result.push(turn);
      continue;
    }
    result[at] = pickTurn(result[at], turn);
  }
  return result;
}

/** 同一条逻辑回合的两份快照：服务端回合优先，本地乐观回合被服务端结果替换。 */
function pickTurn(current: AgentTurn, incoming: AgentTurn): AgentTurn {
  const currentLocal = isLocalTurn(current);
  const incomingLocal = isLocalTurn(incoming);
  if (currentLocal !== incomingLocal) return currentLocal ? incoming : current;
  // 同类时保留较新的一条（compareTurns 升序，排在后面的是更新的）
  const newer = compareTurns(current, incoming) < 0 ? incoming : current;
  const older = newer === incoming ? current : incoming;
  return mergeTurn(older, newer);
}

/** 插入/更新本地乐观回合（按 requestId 去重，同键重试复用同一条） */
export function upsertLocalTurn(turns: AgentTurn[], local: LocalAgentTurn): AgentTurn[] {
  const index = turns.findIndex((turn) => isLocalTurn(turn) && turn.requestId === local.requestId);
  const next = turns.slice();
  if (index === -1) next.push(local);
  else next[index] = local;
  return orderTurns(next);
}

/** 撤下本地乐观回合（发送失败且服务端没有确认时，避免留一条永远 pending 的气泡） */
export function removeLocalTurn(turns: AgentTurn[], requestId: string): AgentTurn[] {
  return turns.filter((turn) => !(isLocalTurn(turn) && turn.requestId === requestId));
}

/**
 * 拉齐服务端最新回合：保留比最新一条更早的本地回合与本地乐观回合，
 * 服务端没有回合时只保留本地乐观回合。
 */
export function mergeServerTurns(existing: AgentTurn[], latest: AgentTurn[]): AgentTurn[] {
  if (!latest.length) return orderTurns(existing.filter(isLocalTurn));
  const first = latest[0].id;
  // HTTP 响应可能晚于同一回合的 WS 帧：已加载过的回合按消息逐条合并，不让旧快照盖掉新状态
  const known = new Map(existing.map((turn) => [turn.id, turn]));
  const kept = existing.filter((turn) => isLocalTurn(turn) || turn.id < first);
  const merged = orderTurns([
    ...kept,
    ...latest.map((turn) => (known.has(turn.id) ? mergeTurn(known.get(turn.id), turn) : turn)),
  ]);
  return dedupeTurns(merged);
}

// ---------------------------------------------------------------------------
// 双 Agent 群聊：逐条回复消息的合并（docs/agent-dual-agent-chat-design.md §4.3）
// ---------------------------------------------------------------------------

/** 带逐条回复的回合（群聊协议）；空数组表示还没路由出谁来说话。旧协议的回合没有这个字段。 */
type GroupTurn = AgentTurn & { messages: AgentReplyMessage[] };

export function hasMessages(turn: AgentTurn): turn is GroupTurn {
  return Array.isArray(turn.messages);
}

/** 只收到过增量、还没有完整快照的消息：缺归属（位置、卡片、来源），完整快照到达时补齐。 */
type PartialMessage = AgentReplyMessage & { partial?: true };

function isPartial(message: AgentReplyMessage): boolean {
  return (message as PartialMessage).partial === true;
}

/**
 * 消息是否比本地已有的更新：版本更高，或同版本且正文更长。
 * 版本随服务端每次持久化变更递增；同一版本里累计正文只会变长（工具轮之后模型重新起头的
 * 替换，服务端先落库再推，一定带更高的版本）。其余情况按乱序或重复的旧帧丢弃。
 */
export function messageIsNewer(incoming: AgentReplyMessage, current?: AgentReplyMessage): boolean {
  if (!current) return true;
  if (incoming.version !== current.version) return incoming.version > current.version;
  return incoming.text.length > current.text.length;
}

function isSettled(message: AgentReplyMessage): boolean {
  return message.status === 'complete' || message.status === 'failed';
}

/** 联网核实之类的瞬时进度只属于生成中的消息；消息结束或被更新的快照覆盖时清掉。 */
function carriedActivity(
  current: AgentReplyMessage | undefined,
  incoming: AgentReplyMessage,
): AgentReplyMessage['activity'] {
  return incoming.status === 'running' ? current?.activity : undefined;
}

/**
 * 合并同一条消息的两个快照。返回合并结果与「本次是否带来了更新」（回合级据此判断
 * 是不是迟到的旧快照）。
 */
function mergeMessage(
  current: AgentReplyMessage | undefined,
  incoming: AgentReplyMessage,
): { message: AgentReplyMessage; advanced: boolean } {
  if (!current) return { message: incoming, advanced: true };
  const activity = carriedActivity(current, incoming);
  if (incoming.readOnly || current.readOnly) {
    // 旧回合的投影消息没有可比较的版本（恒为 1）：卡片状态、已读等变化不体现在正文上，
    // 以服务端快照为准；只有正文变长或状态推进才算「更新」
    const advanced =
      incoming.text.length > current.text.length || (isSettled(incoming) && !isSettled(current));
    return { message: { ...incoming, activity }, advanced };
  }
  if (isPartial(current)) {
    // 只收到过增量的占位：完整快照补齐归属；仍在生成时保留已流出的更长正文
    const ahead = incoming.status === 'running' && current.text.length > incoming.text.length;
    return {
      message: ahead
        ? { ...incoming, text: current.text, version: current.version, activity }
        : incoming,
      advanced: true,
    };
  }
  if (!messageIsNewer(incoming, current)) return { message: current, advanced: false };
  return { message: { ...incoming, activity }, advanced: true };
}

/**
 * 同一回合的两个快照合并：消息按 id 逐条取新。任一侧不带逐条回复（旧协议）时整体以
 * 新来的为准；迟到的旧快照不能把已结束的回合拉回等待——重试会让失败的消息先回到排队，
 * 所以「有消息更新」时才接受回合状态的回退。
 */
export function mergeTurn(current: AgentTurn | undefined, incoming: AgentTurn): AgentTurn {
  if (!current || !hasMessages(incoming) || !hasMessages(current)) return incoming;
  const known = new Map(current.messages.map((message) => [message.id, message] as const));
  const seen = new Set<string>();
  let advanced = false;
  const messages: AgentReplyMessage[] = [];
  for (const message of incoming.messages) {
    seen.add(message.id);
    const result = mergeMessage(known.get(message.id), message);
    advanced = advanced || result.advanced;
    messages.push(result.message);
  }
  for (const message of current.messages) if (!seen.has(message.id)) messages.push(message);
  // 位置未知（只收到过增量）的占位保持到达顺序：两位按发言计划依次生成
  messages.sort((a, b) => a.position - b.position);
  const regressed = current.status !== 'pending' && incoming.status === 'pending' && !advanced;
  return regressed
    ? { ...current, readAt: incoming.readAt ?? current.readAt, messages }
    : { ...incoming, messages };
}

function unknownTurn(id: number, messages: AgentReplyMessage[]): AgentTurn {
  return {
    id,
    requestId: '',
    text: '',
    speaker: 'assistant',
    readAt: null,
    target: 'auto',
    reply: '',
    status: 'pending',
    createdAt: new Date().toISOString(),
    topicId: null,
    replyTo: null,
    sources: [],
    blocks: null,
    images: [],
    messages,
  };
}

function partialMessage(
  messageId: string,
  turnId: number,
  role: AgentReplyMessage['role'],
  text: string,
  version: number,
): PartialMessage {
  return {
    id: messageId,
    turnId,
    role,
    // 位置由路由决定，此时还不知道：排到最后，完整快照到达时补齐
    position: Number.MAX_SAFE_INTEGER,
    kind: 'answer',
    status: 'running',
    text,
    version,
    sources: [],
    blocks: null,
    error: null,
    errorNote: null,
    readOnly: false,
    createdAt: new Date().toISOString(),
    finishedAt: null,
    partial: true,
  };
}

function withMessages(
  turns: AgentTurn[],
  turn: AgentTurn,
  messages: AgentReplyMessage[],
): AgentTurn[] {
  const next = turns.slice();
  next[turns.indexOf(turn)] = { ...turn, messages };
  return next;
}

/**
 * 群聊消息的流式增量（WS message_delta）：累计正文 + 所属版本，按「版本更高或同版本更长」
 * 接受。消息或回合的快照还没到时按 id 建占位，快照到达时补齐归属。
 */
export function applyMessageDeltaToTurns(
  turns: AgentTurn[],
  turnId: number,
  messageId: string,
  role: AgentReplyMessage['role'],
  text: string,
  version: number,
): AgentTurn[] {
  const turn = turns.find((item) => item.id === turnId);
  if (!turn) {
    return orderTurns([
      ...turns,
      unknownTurn(turnId, [partialMessage(messageId, turnId, role, text, version)]),
    ]);
  }
  if (!hasMessages(turn)) return turns;
  const existing = turn.messages.find((message) => message.id === messageId);
  if (existing && !messageIsNewer({ ...existing, text, version }, existing)) return turns;
  if (!existing) {
    // 回合已结束还冒出没见过的消息：不往已收尾的回合里加内容
    if (turn.status !== 'pending') return turns;
    return withMessages(turns, turn, [
      ...turn.messages,
      partialMessage(messageId, turnId, role, text, version),
    ]);
  }
  // 新正文到了，联网核实之类的瞬时进度不再显示
  const next: AgentReplyMessage = {
    ...existing,
    status: 'running',
    text,
    version,
    activity: undefined,
  };
  return withMessages(
    turns,
    turn,
    turn.messages.map((message) => (message.id === messageId ? next : message)),
  );
}

/** 群聊消息快照（WS message：领取、终态、卡片同步）：按 id 并入所属回合。 */
export function applyMessageToTurns(turns: AgentTurn[], message: AgentReplyMessage): AgentTurn[] {
  const turn = turns.find((item) => item.id === message.turnId);
  if (!turn) return orderTurns([...turns, unknownTurn(message.turnId, [message])]);
  const current = hasMessages(turn) ? turn.messages : [];
  const incoming = {
    ...turn,
    messages: [...current.filter((item) => item.id !== message.id), message],
  };
  const next = turns.slice();
  next[turns.indexOf(turn)] = mergeTurn(turn, incoming);
  return next;
}

/** 群聊消息的瞬时状态（WS message_status）：不是正文，只挂在生成中的消息上。 */
export function applyMessageStatusToTurns(
  turns: AgentTurn[],
  turnId: number,
  messageId: string,
  activity: NonNullable<AgentReplyMessage['activity']>,
): AgentTurn[] {
  const turn = turns.find((item) => item.id === turnId);
  if (!turn || !hasMessages(turn)) return turns;
  let changed = false;
  const messages = turn.messages.map((message) => {
    if (message.id !== messageId || message.status !== 'running' || message.activity === activity) {
      return message;
    }
    changed = true;
    return { ...message, activity };
  });
  return changed ? withMessages(turns, turn, messages) : turns;
}

/** 这一轮有只重试失败发言的入口：群聊回合里存在失败的真实消息（旧回合的投影不可重试）。 */
export function canRetryMessages(turn: AgentTurn): boolean {
  return (
    turn.status !== 'pending' &&
    hasMessages(turn) &&
    turn.messages.some((message) => message.status === 'failed' && !message.readOnly)
  );
}

/** 「两位都回」只在服务端确认当前账号开放双 Agent 时可用，否则等同未指定。 */
export function sendableTarget(
  target: AgentTurn['target'],
  dualAgent: boolean,
): AgentTurn['target'] {
  return target === 'both' && !dualAgent ? 'auto' : target;
}

/**
 * 还没路由出谁来说话时，等待占位归谁：只点名了一位（且这条话没有引用别的发言人的消息）时，
 * 谁接话已经确定；「两位都回」、带引用和未指定时先发言的要等路由结果，不预设发言人。
 */
export function placeholderRole(
  target: AgentTurn['target'],
  quoted = false,
): AgentTurn['speaker'] | null {
  if (quoted || target === 'auto' || target === 'both') return null;
  return target;
}

const SPEAKER_MARK = /^\s*【(?:爱宠|照料助手)】\s*/;

/**
 * 这条话引用的是哪一条回复（输入区的引用预览与主人气泡上的引用行共用），与服务端
 * 解析引用的口径一致：消息级引用精确到那一条；只有回合级引用时取该回合最后一条有正文的话。
 * 被引用的回合不在已加载范围、或已没有正文时返回 null（不显示引用）。
 */
export function quotedReply(
  turns: AgentTurn[],
  replyTo: number | null | undefined,
  replyToMessage?: string | null,
): { role: AgentTurn['speaker']; text: string } | null {
  if (!replyTo) return null;
  const turn = turns.find((item) => item.id === replyTo);
  if (!turn) return null;
  const messages = hasMessages(turn) ? turn.messages : null;
  const candidates = messages
    ? replyToMessage
      ? messages.filter((message) => message.id === replyToMessage)
      : messages
    : // 旧协议的回合没有逐条回复：按回合的发言人与正文回退（解析不了消息级引用）
      replyToMessage
      ? []
      : [{ role: turn.speaker, text: turn.reply }];
  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    const text = candidates[index].text.replace(SPEAKER_MARK, '');
    if (text.trim()) return { role: candidates[index].role, text };
  }
  return null;
}

/**
 * 流式增量归属：
 * 1. 命中已落地的服务端回合 → 就地更新（终态回合以服务端已保存结果为准）；
 * 2. 没有落地回合但有本地乐观回合 → 增量归到它身上（发送在途，服务端回合 id 未知）；
 * 3. 都没有 → 建占位回合（终态 turn 帧或 POST 返回会覆盖）。
 */
export function applyDeltaToTurns(
  turns: AgentTurn[],
  turnId: number,
  text: string,
  speaker: AgentTurn['speaker'],
  topicId: number | null = null,
): AgentTurn[] {
  const index = turns.findIndex((item) => item.id === turnId);
  if (index !== -1) {
    const current = turns[index];
    if (current.status !== 'pending') return turns; // 终态以服务端已保存结果为准
    const next = turns.slice();
    next[index] = { ...current, reply: text, speaker };
    return next;
  }
  const localIndex = turns.findIndex((item) => isLocalTurn(item) && item.status === 'pending');
  if (localIndex !== -1) {
    const next = turns.slice();
    const current = next[localIndex];
    next[localIndex] = { ...current, reply: text, speaker };
    return next;
  }
  const placeholder: AgentTurn = {
    id: turnId,
    requestId: '',
    text: '',
    speaker,
    readAt: null,
    target: 'auto',
    reply: text,
    status: 'pending',
    createdAt: new Date().toISOString(),
    topicId,
    replyTo: null,
    sources: [],
    blocks: null,
    images: [],
  };
  return orderTurns([...turns, placeholder]);
}
