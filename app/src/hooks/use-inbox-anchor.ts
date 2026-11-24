/**
 * 收件箱深链锚点（W12，agent-w12-spec.md §5；W16 起锚点带着会话，docs/agent-w16-spec.md §6.2）：
 * 从收件箱、今日预览或通知进入伙伴页时，定位到**那条来源消息**，而不是停在会话末尾。
 * - 锚点已在某个会话的缓存里：直接定位；它不在当前会话就先切过去；
 * - 不在已加载的范围：按稳定回合 id 向服务端精确取回（GET /agent/inbox/turn/<id>）；
 *   它属于别的会话时同样先切过去，再在列表上方单独展示那张卡；
 * - 安静主题没有交付回合：取权威主题本身展示；
 * - 目标**展示之后**才标已读（由列表在定位后调用 markPresented）；账号变更后不应用。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { AgentCareTopic, AgentTurn } from '@/services/agent';
import { fetchInboxItem, fetchInboxTurn, markInboxRead } from '@/services/agent-inbox';
import { getToken } from '@/services/api';
import { anchorKey, findAnchorTurnId, parseAnchorParams } from '@/services/inbox-anchor';

export type InboxAnchorParams = {
  turnId?: string;
  topicId?: string;
  deliveryId?: string;
  inboxId?: string;
};

export function useInboxAnchor(options: {
  params: InboxAnchorParams;
  /** 所有已缓存会话的回合：锚点可能落在当前会话之外 */
  turns: AgentTurn[];
  activeId: number | null;
  /** 切到锚点所在的会话：只改 conversationId，保留定位参数 */
  switchTo: (conversationId: number) => void;
}) {
  const { params, turns, activeId, switchTo } = options;
  const { turnId, topicId, deliveryId, inboxId } = params;
  const target = useMemo(
    () => parseAnchorParams({ turnId, topicId, deliveryId, inboxId }),
    [turnId, topicId, deliveryId, inboxId],
  );
  const identity = anchorKey(target);
  const [fetched, setFetched] = useState<{
    key: string;
    turn: AgentTurn | null;
    topic: AgentCareTopic | null;
    error: string;
  }>({ key: '', turn: null, topic: null, error: '' });
  const [presentedKey, setPresentedKey] = useState('');

  // 本地命中：按稳定回合 id、交付 id 或主题 id 在缓存里找（不依赖当前是哪个会话）
  const localId = useMemo(() => findAnchorTurnId(turns, target), [turns, target]);
  const fresh = fetched.key === identity;

  // 锚点在别的会话：先切过去。切换会立刻把当前会话改成目标，所以这条 effect 不会反复触发
  const anchorConversation =
    localId !== null
      ? (turns.find((turn) => turn.id === localId)?.conversationId ?? null)
      : fresh
        ? (fetched.turn?.conversationId ?? null)
        : null;
  useEffect(() => {
    if (anchorConversation === null || anchorConversation === activeId) return;
    switchTo(anchorConversation);
  }, [anchorConversation, activeId, switchTo]);

  // 取回锚点来源：本地命中就定位，否则按稳定 id 向服务端精确取回（不依赖会话分页）
  useEffect(() => {
    if (target.turnId === null && target.topicId === null && target.deliveryId === null) return;
    if (localId !== null) return;
    let cancelled = false;
    const session = getToken();
    const currentSession = () => !cancelled && !!session && getToken() === session;
    (async () => {
      try {
        if (target.turnId !== null) {
          const data = await fetchInboxTurn(target.turnId);
          if (!currentSession()) return;
          setFetched({ key: identity, turn: data.turn, topic: null, error: '' });
        } else if (target.inboxId !== null) {
          const data = await fetchInboxItem(target.inboxId);
          if (!currentSession()) return;
          setFetched({
            key: identity,
            turn: null,
            topic: (data.item.source?.topic as AgentCareTopic | undefined) ?? null,
            error: '',
          });
        }
      } catch {
        if (!currentSession()) return;
        setFetched({ key: identity, turn: null, topic: null, error: '这条通知的来源暂时打不开' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [identity, localId, target.turnId, target.topicId, target.deliveryId, target.inboxId]);

  const markPresented = useCallback(() => setPresentedKey(identity), [identity]);
  const presented = localId !== null || (fresh && presentedKey === identity);

  // 目标已展示 → 标已读（展示失败或账号变更后不标，保持未读）
  useEffect(() => {
    if (presented && target.inboxId !== null)
      void markInboxRead(target.inboxId).catch(() => undefined);
  }, [presented, target.inboxId]);

  return {
    identity,
    localId,
    /** 不在缓存里、单独取回的来源消息（展示在列表上方） */
    turn: fresh ? fetched.turn : null,
    topic: fresh ? fetched.topic : null,
    error: fresh ? fetched.error : '',
    markPresented,
  };
}

export type InboxAnchor = ReturnType<typeof useInboxAnchor>;
