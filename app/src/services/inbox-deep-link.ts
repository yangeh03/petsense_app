/**
 * 收件箱深链缝（W13 ↔ W12）：把「稳定的收件箱 ID」解析成应用内路由。
 *
 * W13 的职责到此为止：通知载荷里只带稳定的收件箱 ID（服务端保证同一 ID 指向同一
 * 对象），客户端负责把它交给 W12 的统一解析（前台、后台、冷启动走同一条路径）。
 *
 * 合入 W12 时只需做一件事：
 *
 * ```ts
 * import { setInboxDeepLinkResolver } from '@/services/inbox-deep-link';
 *
 * setInboxDeepLinkResolver((itemId) => {
 *   // 用 W12 实际提交的路由与参数格式定位原始对象
 *   return `/agent-inbox?item=${encodeURIComponent(itemId)}`;
 * });
 * ```
 *
 * 未安装解析器时，`resolveInboxDeepLink` 返回默认路由（`INBOX_DEEP_LINK_ROUTE`）；
 * 若该路由在当前构建中不存在，调用方会回退到伙伴页，不会崩。
 */

/** W12 收件箱路由：合入时按 W12 实际提交的路由名调整（或改用上面的解析器）。 */
export const INBOX_DEEP_LINK_ROUTE = '/agent-inbox';

/** 解析失败时的兜底页（当前构建里一定存在）。 */
export const INBOX_FALLBACK_ROUTE = '/agent';

export type InboxDeepLinkResolver = (inboxItemId: string) => string | null;

let resolver: InboxDeepLinkResolver | null = null;

/** 安装 W12 的解析器（合入时调用一次即可）。 */
export function setInboxDeepLinkResolver(next: InboxDeepLinkResolver | null) {
  resolver = next;
}

/** 稳定的收件箱 ID → 应用内路由。返回 null 表示无法定位。 */
export function resolveInboxDeepLink(inboxItemId: string): string | null {
  const itemId = (inboxItemId ?? '').trim();
  if (!itemId) return null;
  if (resolver) {
    try {
      return resolver(itemId);
    } catch {
      return null;
    }
  }
  return `${INBOX_DEEP_LINK_ROUTE}?item=${encodeURIComponent(itemId)}`;
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
