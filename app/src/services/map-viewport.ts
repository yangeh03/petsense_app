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

export function positionMatchesMap(position: UwbPosition | null, map: UwbMap | null) {
  return !!position && !!map && position.mapId === map.mapId && position.mapVersion === map.version;
}

/**
 * Rect (in stage points) of the raster background, replicating the SVG viewBox
 * "xMidYMid meet" fit. The bitmap renders as a native image because react-native-svg
 * decodes data-URI images at layout-point size, losing device-pixel sharpness.
 */
export function backgroundImageRect(
  map: UwbMap,
  bounds: { minX: number; minY: number; width: number; height: number },
  viewport: { width: number; height: number },
) {
  if (!viewport.width || !viewport.height || !bounds.width || !bounds.height) return null;
  const { origin, width: pixelWidth, height: pixelHeight, metersPerPixel } = map.background;
  const imageWidth = pixelWidth * metersPerPixel;
  const imageHeight = pixelHeight * metersPerPixel;
  const scale = Math.min(viewport.width / bounds.width, viewport.height / bounds.height);
  const offsetX = (viewport.width - bounds.width * scale) / 2;
  const offsetY = (viewport.height - bounds.height * scale) / 2;
  return {
    left: offsetX + (origin[0] - bounds.minX) * scale,
    top: offsetY + (bounds.minY + bounds.height - origin[1] - imageHeight) * scale,
    width: imageWidth * scale,
    height: imageHeight * scale,
  };
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
