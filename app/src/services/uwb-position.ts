/**
 * UWB 三边定位：≥3 个「锚点坐标 + 距离」用高斯-牛顿迭代解二维位置。
 * 纯函数无依赖，方便单测（telemetry-check 里有回代验证）。
 */

import { FLOOR_ANCHORS, METERS_TO_PX } from '@/constants/uwb-map';
import type { TelemetryUwb } from '@/types/telemetry';

export type AnchorReading = {
  x: number;
  y: number;
  /** 距离（px，户型图坐标系） */
  distance: number;
};

const ITERATIONS = 12;

/** 高斯-牛顿解最小二乘位置；残差过大或输入不足返回 null */
export function trilaterate(readings: AnchorReading[]): { x: number; y: number } | null {
  if (readings.length < 3) return null;
  let x = readings.reduce((sum, r) => sum + r.x, 0) / readings.length;
  let y = readings.reduce((sum, r) => sum + r.y, 0) / readings.length;

  for (let iteration = 0; iteration < ITERATIONS; iteration++) {
    // 正规方程 (JᵀJ) Δ = Jᵀr
    let a11 = 0;
    let a12 = 0;
    let a22 = 0;
    let b1 = 0;
    let b2 = 0;
    for (const reading of readings) {
      const dx = x - reading.x;
      const dy = y - reading.y;
      const estimate = Math.hypot(dx, dy);
      if (estimate < 1e-6) continue;
      const residual = estimate - reading.distance;
      const j1 = dx / estimate;
      const j2 = dy / estimate;
      a11 += j1 * j1;
      a12 += j1 * j2;
      a22 += j2 * j2;
      b1 -= j1 * residual;
      b2 -= j2 * residual;
    }
    const determinant = a11 * a22 - a12 * a12;
    if (Math.abs(determinant) < 1e-9) break;
    const deltaX = (a22 * b1 - a12 * b2) / determinant;
    const deltaY = (a11 * b2 - a12 * b1) / determinant;
    x += deltaX;
    y += deltaY;
    if (Math.abs(deltaX) + Math.abs(deltaY) < 0.01) break;
  }

  let cost = 0;
  for (const reading of readings) {
    cost += (Math.hypot(x - reading.x, y - reading.y) - reading.distance) ** 2;
  }
  if (cost > 90 ** 2) return null; // 距离自相矛盾（残差平方和超阈值）视为无效
  return { x, y };
}

/** 从 UWB 帧提取锚点读数并解算位置；无有效距离返回 null */
export function positionFromUwb(uwb: TelemetryUwb | undefined): { x: number; y: number } | null {
  if (!uwb?.valid) return null;
  const distances = uwb.kalmanEnabled && uwb.kalmanMm.length > 0 ? uwb.kalmanMm : uwb.distanceMm;
  const readings: AnchorReading[] = [];
  FLOOR_ANCHORS.forEach((anchor, index) => {
    const mm = distances[index];
    if (typeof mm === 'number' && mm > 0 && mm < 30_000) {
      readings.push({ x: anchor.x, y: anchor.y, distance: (mm / 1000) * METERS_TO_PX });
    }
  });
  return trilaterate(readings);
}
