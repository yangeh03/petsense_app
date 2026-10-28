import type { UwbMap, UwbPosition } from '@/types/uwb';

/** View transforms never write back to the measured map coordinates. */
export function mapBounds(map: UwbMap) {
  const { origin, width, height, metersPerPixel } = map.background;
  const points = [
    origin,
    [origin[0] + width * metersPerPixel, origin[1] + height * metersPerPixel],
    ...map.polygon,
    ...map.anchors.map((anchor) => anchor.position),
  ];
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  return { minX, minY, width: Math.max(...xs) - minX, height: Math.max(...ys) - minY };
}

export function clampMapPan(
  pan: { x: number; y: number },
  viewport: { width: number; height: number },
  bounds: { width: number; height: number },
  zoom: number,
) {
  if (!viewport.width || !viewport.height || !bounds.width || !bounds.height) return { x: 0, y: 0 };
  const fit = Math.min(viewport.width / bounds.width, viewport.height / bounds.height);
  const maxX = Math.max(0, (bounds.width * fit * zoom - viewport.width) / 2);
  const maxY = Math.max(0, (bounds.height * fit * zoom - viewport.height) / 2);
  return {
    x: Math.max(-maxX, Math.min(maxX, pan.x)) || 0,
    y: Math.max(-maxY, Math.min(maxY, pan.y)) || 0,
  };
}

/** Place the raster in screen points using exactly the same fit and Y flip as the SVG overlay. */
export function mapImageRect(map: UwbMap, viewport: { width: number; height: number }) {
  const bounds = mapBounds(map);
  const bg = map.background;
  const fit = Math.min(viewport.width / bounds.width, viewport.height / bounds.height);
  return {
    left: (viewport.width - bounds.width * fit) / 2 + (bg.origin[0] - bounds.minX) * fit,
    top:
      (viewport.height - bounds.height * fit) / 2 +
      (bounds.minY + bounds.height - bg.origin[1] - bg.height * bg.metersPerPixel) * fit,
    width: bg.width * bg.metersPerPixel * fit,
    height: bg.height * bg.metersPerPixel * fit,
  };
}

export function positionMatchesMap(position: UwbPosition | null, map: UwbMap | null) {
  return !!position && !!map && position.mapId === map.mapId && position.mapVersion === map.version;
}

export function appendMeasuredTrail(
  trail: { x: number; y: number }[],
  position: UwbPosition | null,
  lastMeasurement: string,
) {
  if (
    !position?.fresh ||
    position.status !== 'ok' ||
    !position.position ||
    position.quality?.insideMap === false
  )
    return { trail, lastMeasurement };
  const key = `${position.mapId}:${position.mapVersion}:${position.expiresAt}:${position.sequence}`;
  if (key === lastMeasurement) return { trail, lastMeasurement };
  return { trail: [...trail.slice(-79), { ...position.position }], lastMeasurement: key };
}
