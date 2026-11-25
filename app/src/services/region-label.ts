import type { UwbMap, UwbPosition } from '@/types/uwb';

export function regionLabel(position: UwbPosition | null, map: UwbMap, fresh: boolean): string {
  if (!position?.position) return '等待定位';
  if (
    position.mapId !== map.mapId ||
    position.mapVersion !== map.version ||
    (position.regionsRevision ?? 'none') !== (map.regionsRevision ?? 'none')
  )
    return '区域信息更新中';
  if (!fresh)
    return position.region ? `上次所在：${position.region.label}` : '定位已过期，区域待确认';
  switch (position.regionStatus) {
    case 'known':
      return position.region?.label ?? '区域待确认';
    case 'transition':
      return '区域边界附近';
    case 'unmapped':
      return '未命名区域';
    case 'uncertain':
      return '定位质量不足，区域待确认';
    case 'waiting':
      return '等待新定位确认区域';
    default:
      return '尚未设置区域';
  }
}
