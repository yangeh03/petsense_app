/**
 * UWB 锚点与户型图坐标映射。
 * 坐标系与 FloorPlan 的 900×800 viewBox 一致；METERS_TO_PX 决定距离换算比例
 * （户型宽 900px ≈ 9m 客厅横向尺度）。真实部署锚点后改这里即可，无需动页面。
 */

export const FLOOR_ANCHORS = [
  { id: 0, x: 120, y: 120 },
  { id: 1, x: 780, y: 140 },
  { id: 2, x: 140, y: 660 },
  { id: 3, x: 760, y: 680 },
] as const;

/** 像素/米：900px 视作约 9m 宽的户型 */
export const METERS_TO_PX = 100;
