/**
 * 关怀主题的状态说法（W09）。关怀卡与来自收件箱的锚点卡共用，界面里不出现 open / closed 这类内部状态名。
 */

const STATUS_LABELS: Record<string, string> = {
  open: '提醒中',
  ongoing: '持续关注',
  recovered: '已恢复',
  closed: '已关闭',
};

/** 已知状态给中文说法；未知状态返回空串，调用方据此不显示这一行。 */
export function careStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? '';
}
