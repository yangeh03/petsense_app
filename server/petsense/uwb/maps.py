"""Validated, versioned map bundles. Runtime bindings stay outside Git."""
import base64
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import zlib


@dataclass(frozen=True)
class Filters:
    median_window: int = 5
    dist_ema_static: float = .16
    dist_ema_moving: float = .48
    dist_outlier_static_m: float = .12
    dist_outlier_moving_m: float = .30
    dist_max_step_static_m: float = .04
    dist_max_step_moving_m: float = .12
    dist_motion_band_m: float = .10
    pos_smooth_static: float = .18
    pos_smooth_moving: float = .52
    pos_deadband_static_m: float = .020
    pos_deadband_moving_m: float = .006
    pos_motion_band_m: float = .08


@dataclass(frozen=True)
class EngineConfig:
    anchor_indices: tuple
    anchors_uwb_m: tuple
    map_polygon_m: tuple
    transform_scale: float
    transform_matrix: tuple
    transform_translation_m: tuple
    stale_after_seconds: float
    min_distance_m: float = .05
    max_distance_m: float = 30.
    max_residual_m: float = .35
    filters: Filters = Filters()


def number(value, low=-1e6, high=1e6):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid finite map number')
    return float(value)


def xy(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('Expected XY pair')
    return tuple(number(v) for v in value)


def validate_config(d):
    """Pure geometry contract shared with the offline exporter; no Flask/runtime imports."""
    if not isinstance(d, dict):
        raise ValueError('Map manifest must be an object')
    if d.get('schemaVersion') != 1 or d.get('units') != 'm':
        raise ValueError('Unsupported map schema or units')
    for key in ['mapId', 'version']:
        if not isinstance(d.get(key), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', d[key]):
            raise ValueError('Invalid map identity')
    polygon = tuple(xy(p) for p in d['polygon'])
    if not 3 <= len(polygon) <= 10000:
        raise ValueError('Invalid map polygon')
    area = sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))
    if abs(area) < 1e-6:
        raise ValueError('Degenerate map polygon')
    anchors = d['anchors']
    if not isinstance(anchors, list) or not all(isinstance(a, dict) for a in anchors):
        raise ValueError('Invalid anchor list')
    indices = tuple(a['id'] for a in anchors)
    if len(indices) != 3 or len(set(indices)) != 3 or any(type(i) is not int or not 0 <= i < 8 for i in indices):
        raise ValueError('Exactly three distinct anchor indices are required')
    points = tuple(xy(a['uwb']) for a in anchors)
    a, b, c = points
    if abs((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])) < 1e-6:
        raise ValueError('Collinear anchors')
    t = d['uwbTransform']
    if not isinstance(t, dict):
        raise ValueError('Invalid UWB transform')
    matrix = tuple(xy(row) for row in t['matrix'])
    if len(matrix) != 2 or abs(matrix[0][0]*matrix[1][1]-matrix[0][1]*matrix[1][0]) < 1e-9:
        raise ValueError('Singular map transform')
    scale = number(t['scale'], .000001, 1000)
    translation = xy(t['translation'])
    stale = number(d.get('staleAfterMs', 2000), 100, 30000) / 1000
    return EngineConfig(indices, points, polygon, scale, matrix, translation, stale)


class MapBundle:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        raw = (self.directory / 'map.json').read_bytes()
        if len(raw) > 1_000_000:
            raise ValueError('Map manifest is too large')
        self.data = json.loads(raw)
        d = self.data
        self.config = validate_config(d)
        bg = d['background']
        if not isinstance(bg, dict):
            raise ValueError('Invalid image metadata')
        self.image = (self.directory / bg['file']).resolve()
        if not self.image.is_relative_to(self.directory) or self.image.suffix.lower() != '.png':
            raise ValueError('Map image must be a local PNG')
        image = self.image.read_bytes()
        if len(image) > 4_000_000 or image[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Invalid or oversized map image')
        for key in ['width', 'height']:
            if type(bg[key]) is not int or not 1 <= bg[key] <= 10000:
                raise ValueError('Invalid image dimensions')
        if bg['width'] * bg['height'] > 20_000_000:
            raise ValueError('Map image has too many pixels')
        if tuple(int.from_bytes(image[i:i+4], 'big') for i in [16, 20]) != (bg['width'], bg['height']):
            raise ValueError('Image dimensions do not match manifest')
        self.origin = xy(bg['origin'])
        self.resolution = number(bg['metersPerPixel'], .0001, 10)
        self.fingerprint = hashlib.sha256(raw + image).hexdigest()
        self.image_bytes = image

    def public(self):
        d, c = self.data, self.config
        def point(p):
            return [c.transform_scale * sum(c.transform_matrix[i][j]*p[j] for j in range(2)) + c.transform_translation_m[i] for i in range(2)]
        return {'schemaVersion': 1, 'mapId': d['mapId'], 'version': d['version'],
                'name': d.get('name', d['mapId']), 'units': 'm', 'polygon': c.map_polygon_m,
                'anchors': [{'id': i, 'position': point(p)} for i, p in zip(c.anchor_indices, c.anchors_uwb_m)],
                'background': {**d['background'],
                               'byteLength': len(self.image_bytes),
                               'crc32': f'{zlib.crc32(self.image_bytes):08x}',
                               'imageDataUri': 'data:image/png;base64,' + base64.b64encode(self.image_bytes).decode()},
                'staleAfterMs': round(c.stale_after_seconds * 1000)}


class Registry:
    def __init__(self, bindings):
        self.path = Path(bindings)
        self.cache = {}

    def for_device(self, device):
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 100_000:
            raise ValueError('Bindings file is too large')
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('devices'), dict):
            raise ValueError('Bindings must contain a devices object')
        entry = data['devices'].get(device)
        if not entry:
            return None
        if not isinstance(entry, dict):
            raise ValueError('Invalid device binding')
        root = self.path.parent.resolve()
        directory = (root / entry['directory']).resolve()
        if not directory.is_relative_to(root):
            raise ValueError('Map binding escapes map root')
        # Revalidate changed assets; stable maps are reused for high-rate telemetry.
        manifest = directory / 'map.json'
        stamp = manifest.stat().st_mtime_ns
        cached = self.cache.get(directory)
        if not cached or cached[0] != stamp:
            bundle = MapBundle(directory)
            self.cache[directory] = (stamp, bundle)
        bundle = self.cache[directory][1]
        tag = entry['tagId']
        if type(tag) is not int or not 0 <= tag <= 65535:
            raise ValueError('Invalid bound Tag')
        return bundle, tag
