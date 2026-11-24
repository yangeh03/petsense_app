/**
 * 收件箱深链缝（W13 → W12）：把「稳定的收件箱 ID」解析成应用内路由。
 *
 * W13 的职责到此为止：通知载荷里只带稳定的收件箱 ID（服务端保证同一 ID 指向同一
 * 对象）。路由一律指向 W12 的权威解析页 `/inbox/[id]`——登录续接、来源不可用、
 * 账号变更与「展示后才标已读」都由该页统一处理，W13 不维护第二套解析状态。
 *
 * 解析失败（空 ID）返回 null，调用方回退到伙伴页，不会崩。
 */

/** W12 权威深链解析页：按稳定收件箱 ID 向服务端解析目标后跳转。 */
export const INBOX_DEEP_LINK_ROUTE = '/inbox';

/** 解析失败时的兜底页（当前构建里一定存在）。 */
export const INBOX_FALLBACK_ROUTE = '/agent';

/** 稳定的收件箱 ID → W12 权威解析页路由。返回 null 表示无法定位。 */
export function resolveInboxDeepLink(inboxItemId: string): string | null {
  const itemId = (inboxItemId ?? '').trim();
  if (!itemId) return null;
  return `${INBOX_DEEP_LINK_ROUTE}/${itemId}`;
}

/** 从通知载荷里取稳定的收件箱 ID（服务端只带这一个稳定键）。 */
export function readInboxItemId(data: unknown): string | null {
  if (!data || typeof data !== 'object') return null;
  const record = data as Record<string, unknown>;
  for (const key of ['inboxItemId', 'inbox_item_id', 'itemId']) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}
