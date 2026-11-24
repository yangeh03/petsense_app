/**
 * 伙伴会话冷启动时的滚动位置（W14 F1）。
 *
 * 本机有这个账号的会话快照，就回到上次的位置；没有快照（换机或重装后第一次进伙伴页），
 * 或者快照里没有回合（上次没拉到聊天），就跟到最新一条——否则会停在已加载窗口里最旧的消息上。
 */

type RestoreSnapshot = { scrollY?: number; turns?: unknown[] } | null;

export function scrollRestorePlan(snapshot: RestoreSnapshot): {
  followLatest: boolean;
  scrollY: number;
} {
  if (!snapshot) return { followLatest: true, scrollY: 0 };
  return { followLatest: !snapshot.turns?.length, scrollY: snapshot.scrollY ?? 0 };
}
