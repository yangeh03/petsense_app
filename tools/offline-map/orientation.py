"""Rigid floor-to-map rotation: retain the real polygon, distances and handedness."""
import math

import numpy as np

from uwb_map_geometry import convex_hull_2d


def rotation(degrees):
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s], [s, c]])


def normalize(degrees):
    result = (degrees + 180) % 360 - 180
    return 0.0 if abs(result) < 1e-10 else float(result)


def horizontal_angle(points, degrees):
    extent = np.ptp(points @ rotation(degrees).T, axis=0)
    if extent[0] < extent[1] - 1e-9:
        degrees += 90
    # A room's main axis has a 180-degree ambiguity; prefer the smaller turn.
    return float((degrees + 90) % 180 - 90)


def box_angle(points):
    hull = np.asarray(convex_hull_2d(points))
    if len(hull) < 3:
        raise ValueError('Cannot orient a degenerate polygon')
    best = None
    for edge in np.roll(hull, -1, axis=0) - hull:
        angle = horizontal_angle(points, -math.degrees(math.atan2(edge[1], edge[0])))
        extent = np.ptp(points @ rotation(angle).T, axis=0)
        area = float(np.prod(extent))
        if best is None or area < best[0] - max(1e-9, best[0] * 1e-9):
            best = (area, angle)
        elif abs(area - best[0]) <= max(1e-9, best[0] * 1e-9) and abs(angle) < abs(best[1]):
            best = (area, angle)
    return best[1]


def choose_orientation(polygon, mode='auto', extra_degrees=0):
    points = np.asarray(polygon, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise ValueError('Orientation requires a finite polygon')
    area = abs(np.sum(points[:, 0] * np.roll(points[:, 1], -1) - points[:, 1] * np.roll(points[:, 0], -1)))
    if area < 1e-6:
        raise ValueError('Cannot orient a degenerate polygon')
    if mode not in ('auto', 'none') or not math.isfinite(extra_degrees) or abs(extra_degrees) > 360:
        raise ValueError('Invalid orientation mode or extra rotation (must be within +/-360 degrees)')
    angle, coherence, method = 0.0, None, 'manual'
    if mode == 'auto':
        edges = np.roll(points, -1, axis=0) - points
        lengths = np.linalg.norm(edges, axis=1)
        keep = lengths >= max(.1, float(lengths.max()) * .2)
        if keep.any():
            theta = np.arctan2(edges[keep, 1], edges[keep, 0])
            weight = lengths[keep] ** 2
            # Folding by 90 degrees combines perpendicular wall directions;
            # long edges dominate over the raster staircase/furniture notches.
            direction = np.sum(weight * np.exp(4j * theta))
            coherence = float(abs(direction) / weight.sum())
        if coherence is not None and coherence >= .75:
            angle = horizontal_angle(points, -math.degrees(float(np.angle(direction))) / 4)
            method = 'dominant-long-edges'
        else:
            angle = box_angle(points)
            method = 'minimum-area-hull-box'
    degrees = normalize(angle + extra_degrees)
    matrix = rotation(degrees)
    pivot = (points.min(axis=0) + points.max(axis=0)) / 2
    translation = pivot - matrix @ pivot
    extent = np.ptp(points @ matrix.T, axis=0)
    record = {'mode': mode, 'method': method, 'autoRotationDeg': normalize(angle),
              'extraRotationDeg': extra_degrees, 'rotationDeg': degrees,
              'pivotM': pivot.tolist(), 'matrix': matrix.tolist(), 'translationM': translation.tolist(),
              'wallCoherence': coherence,
              'ambiguous': bool(mode == 'auto' and (method == 'minimum-area-hull-box' or
                                  max(extent) / max(min(extent), 1e-12) < 1.05)),
              'sourceFrame': 'confirmed-floor', 'targetFrame': 'map'}
    return matrix, translation, record


def oriented_canvas(low, extent, resolution, matrix, translation):
    if np.allclose(matrix, np.eye(2), atol=1e-12) and np.allclose(translation, 0, atol=1e-12):
        return low, extent
    corners = np.array([low, low + [extent[0], 0], low + extent, low + [0, extent[1]]])
    corners = corners @ matrix.T + translation
    # A one-pixel margin and outward rounding prevent clipping after rotation.
    minimum = np.floor(corners.min(axis=0) / resolution - 1) * resolution
    maximum = np.ceil(corners.max(axis=0) / resolution + 1) * resolution
    return minimum, maximum - minimum


def compose_uwb_transform(transform, matrix, translation):
    return {'scale': transform['scale'],
            'matrix': (matrix @ np.asarray(transform['matrix'])).tolist(),
            'translation': (matrix @ np.asarray(transform['translation']) + translation).tolist()}
