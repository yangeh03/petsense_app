/**
 * W12 深链锚点解析（纯函数，便于定点测试）。
 *
 * 收件箱深链按稳定 id 解析到**具体那条来源消息**。锚点可能：
 * - 在已加载的会话回合里（可直接定位）；
 * - 早于已加载的一页（会话分页只取最近 40 条）或被当前话题过滤掉
 *   → 必须按稳定回合 id 向服务端精确取回（`GET /agent/inbox/turn/<id>`）。
 *
 * 注意：查找范围是**全部已加载回合**，不是被话题过滤后的可见列表。
 */

export type AnchorParams = {
  turnId?: string;
  topicId?: string;
  deliveryId?: string;
  inboxId?: string;
};

export type AnchorTarget = {
  turnId: number | null;
  topicId: number | null;
  deliveryId: number | null;
  inboxId: number | null;
};

type TurnLike = {
  id: number;
  topicId?: number | null;
  blocks?:
    | (
        | { type: 'text' }
        | { type: 'proposal' }
        | { type: 'action' }
        | { type: 'task_result'; task: { deliveryId: number } }
        | { type: 'care'; care: { topicId?: number | null } }
      )[]
    | null;
};

function toId(value: string | undefined | null): number | null {
  if (value === undefined || value === null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/** 归一化深链参数（query 值可能是字符串或字符串数组）。 */
export function parseAnchorParams(params: {
  turnId?: string | string[];
  topicId?: string | string[];
  deliveryId?: string | string[];
  inboxId?: string | string[];
}): AnchorTarget {
  const pick = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);
  return {
    turnId: toId(pick(params.turnId)),
    topicId: toId(pick(params.topicId)),
    deliveryId: toId(pick(params.deliveryId)),
    inboxId: toId(pick(params.inboxId)),
  };
}

/** 在已加载回合里找锚点：turnId → deliveryId → topicId（不依赖话题过滤）。 */
export function findAnchorTurnId(turns: TurnLike[], target: AnchorTarget): number | null {
  if (target.turnId !== null) {
    const found = turns.find((turn) => turn.id === target.turnId);
    return found ? found.id : null;
  }
  if (target.deliveryId !== null) {
    const found = turns.find((turn) =>
      (turn.blocks ?? []).some(
        (block) => block.type === 'task_result' && block.task.deliveryId === target.deliveryId,
      ),
    );
    return found ? found.id : null;
  }
  if (target.topicId !== null) {
    const found = turns.find(
      (turn) =>
        turn.topicId === target.topicId ||
        (turn.blocks ?? []).some(
          (block) => block.type === 'care' && block.care.topicId === target.topicId,
        ),
    );
    return found ? found.id : null;
  }
  return null;
}

/** 是否需要向服务端精确取回来源（不在已加载回合里 / 安静主题没有交付回合）。 */
export function needsRemoteAnchor(turns: TurnLike[], target: AnchorTarget): boolean {
  if (target.turnId === null && target.deliveryId === null && target.topicId === null) {
    return false;
  }
  return findAnchorTurnId(turns, target) === null;
}

/** 锚点身份是否变化（用于重置「已定位」状态）。 */
export function anchorKey(target: AnchorTarget): string {
  return [target.turnId, target.topicId, target.deliveryId, target.inboxId].join(':');
}
