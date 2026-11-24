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

import type { AgentTurn } from '@/services/agent';

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
  if (index === -1) return orderTurns([...kept, incoming]);
  const next = kept.slice();
  next[index] = incoming;
  return orderTurns(next);
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
  const kept = existing.filter((turn) => isLocalTurn(turn) || turn.id < first);
  return orderTurns([...kept, ...latest]);
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
