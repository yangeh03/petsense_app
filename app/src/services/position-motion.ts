export type Point = { x: number; y: number };
type Polygon = readonly (readonly [number, number])[];

export function nearestBoundary(point: Point, polygon: Polygon): Point {
  let best = point;
  let distance = Infinity;
  polygon.forEach((a, i) => {
    const b = polygon[(i + 1) % polygon.length];
    const dx = b[0] - a[0],
      dy = b[1] - a[1];
    const length = dx * dx + dy * dy;
    const t = length
      ? Math.max(0, Math.min(1, ((point.x - a[0]) * dx + (point.y - a[1]) * dy) / length))
      : 0;
    const candidate = { x: a[0] + t * dx, y: a[1] + t * dy };
    const next = Math.hypot(point.x - candidate.x, point.y - candidate.y);
    if (next < distance) {
      best = candidate;
      distance = next;
    }
  });
  return best;
}

export function insideBoundary(point: Point, polygon: Polygon): boolean {
  const edge = nearestBoundary(point, polygon);
  if (Math.hypot(point.x - edge.x, point.y - edge.y) <= 1e-9) return true;
  let inside = false;
  polygon.forEach((a, i) => {
    const b = polygon[(i + 1) % polygon.length];
    if (
      a[1] > point.y !== b[1] > point.y &&
      point.x < ((b[0] - a[0]) * (point.y - a[1])) / (b[1] - a[1]) + a[0]
    )
      inside = !inside;
  });
  return inside;
}

/** Stop at the first exit; endpoints alone cannot detect a concave notch. */
export function motionTarget(start: Point | null, target: Point, polygon: Polygon): Point {
  if (!start || !insideBoundary(start, polygon))
    return insideBoundary(target, polygon) ? target : nearestBoundary(target, polygon);
  const dx = target.x - start.x,
    dy = target.y - start.y;
  const cuts = [0, 1];
  polygon.forEach((a, i) => {
    const b = polygon[(i + 1) % polygon.length];
    const ex = b[0] - a[0],
      ey = b[1] - a[1],
      den = dx * ey - dy * ex;
    if (Math.abs(den) < 1e-12) return;
    const ax = a[0] - start.x,
      ay = a[1] - start.y;
    const t = (ax * ey - ay * ex) / den,
      u = (ax * dy - ay * dx) / den;
    if (t >= 0 && t <= 1 && u >= -1e-9 && u <= 1 + 1e-9) cuts.push(t);
  });
  cuts.sort((a, b) => a - b);
  for (let i = 0; i < cuts.length - 1; i++) {
    const mid = (cuts[i] + cuts[i + 1]) / 2;
    if (!insideBoundary({ x: start.x + mid * dx, y: start.y + mid * dy }, polygon))
      return { x: start.x + cuts[i] * dx, y: start.y + cuts[i] * dy };
  }
  return target;
}

export function motionDuration(remainingMs: number): number {
  return Math.max(0, Math.min(850, remainingMs));
}
