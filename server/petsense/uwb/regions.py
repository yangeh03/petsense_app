"""Reviewed virtual areas, scoped to a device claim and exact calibrated map."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4

from .boundary import contains, constrain, nearest
from .maps import xy


class RegionConflict(ValueError):
    pass


def validate_regions(items, outline):
    if not isinstance(items, list) or len(items) > 64:
        raise ValueError('区域必须为数组，最多 64 个')
    result, ids = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('区域格式无效')
        ident, label = item.get('id'), item.get('label')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', ident) or ident in ids:
            raise ValueError('区域编号无效或重复')
        if not isinstance(label, str) or not label.strip() or len(label) > 40 or any(ord(c) < 32 for c in label):
            raise ValueError('区域名称须为 1～40 字')
        priority = item.get('priority', 0)
        if type(priority) is not int or not -100 <= priority <= 100 or type(item.get('confirmed')) is not bool:
            raise ValueError('请设置区域优先级和确认状态')
        vertices = item.get('polygon')
        if not isinstance(vertices, list) or not 3 <= len(vertices) <= 256:
            raise ValueError('区域须含 3～256 个顶点')
        poly = tuple(xy(v) for v in vertices)
        edges = list(zip(poly, poly[1:] + poly[:1]))
        area = sum(a[0]*b[1]-b[0]*a[1] for a, b in edges)
        if abs(area) < 1e-6 or len(set(poly)) != len(poly):
            raise ValueError('区域轮廓退化或含重复顶点')
        for i, (a, b) in enumerate(edges):
            if not contains(a, outline) or math.dist(constrain(b, outline, a), b) > 1e-7:
                raise ValueError('区域边界必须位于地图轮廓内')
            for j in range(i+1, len(edges)):
                if j == i+1 or (i == 0 and j == len(edges)-1):
                    continue
                c, d = edges[j]
                def cross(p, q, r):
                    return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
                # Reject touching/overlapping nonadjacent edges as well.
                if (max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
                        and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))
                        and cross(a, b, c)*cross(a, b, d) <= 0 and cross(c, d, a)*cross(c, d, b) <= 0):
                    raise ValueError('区域边界不能自相交')
        ids.add(ident)
        result.append({'id': ident, 'label': label.strip(), 'priority': priority,
                       'confirmed': item['confirmed'], 'polygon': [list(p) for p in poly]})
    return result


class RegionStore:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, device, identity):
        key = hashlib.sha256(json.dumps([device, identity], separators=(',', ':')).encode()).hexdigest()
        return self.root / (key + '.json')

    def load(self, device, identity):
        path = self.path(device, identity)
        if not path.exists():
            return {'revision': 'none', 'regions': []}
        raw = path.read_bytes()
        if len(raw) > 150_000:
            raise ValueError('区域配置过大')
        return {'revision': hashlib.sha256(raw).hexdigest(), 'regions': json.loads(raw)['regions']}

    def save(self, device, identity, base_revision, regions):
        if self.load(device, identity)['revision'] != base_revision:
            raise RegionConflict('区域已被更新，请重新读取后修改')
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(device, identity)
        temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
        try:
            with temp.open('x', encoding='utf-8') as f:
                json.dump({'regions': regions}, f, ensure_ascii=False, separators=(',', ':'))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        return self.load(device, identity)


class RegionTracker:
    def __init__(self):
        self.revision = None
        self.active = None
        self.pending = None
        self.pending_since = 0.
        self.count = 0
        self.output = {'region': None, 'regionStatus': 'waiting'}

    def update(self, config, point, now, reliable=True):
        if self.revision != config['revision']:
            self.__init__()
            self.revision = config['revision']
        regions = [r for r in config['regions'] if r['confirmed']]
        if not regions:
            self.output = {'region': None, 'regionStatus': 'unconfigured'}
            return self.output
        if point is None or not reliable:
            self.pending, self.count = None, 0
            self.output = {'region': None, 'regionStatus': 'uncertain'}
            return self.output
        candidates = sorted((r for r in regions if contains(point, r['polygon'])),
                            key=lambda r: (-r['priority'], r['id']))
        choice = candidates[0] if candidates else None
        ident = choice['id'] if choice else None
        active_id = self.active['id'] if self.active else None
        transition = False
        if self.output['regionStatus'] in ('waiting', 'unconfigured'):
            self.active = choice
        elif ident != active_id:
            # A higher-priority area still needs two distinct measurements.
            if self.pending != ident or self.count == 0:
                self.pending, self.pending_since, self.count = ident, now, 1
            else:
                self.count += 1
            near_edge = self.active and math.dist(point, nearest(point, self.active['polygon'])) < .15
            if self.count >= 2 and now-self.pending_since >= .8 and not near_edge:
                self.active, self.count = choice, 0
            else:
                transition = True
        else:
            self.pending, self.count = None, 0
        self.output = {'region': {'id': self.active['id'], 'label': self.active['label']} if self.active else None,
                       'regionStatus': 'transition' if transition else 'known' if self.active else 'unmapped'}
        return self.output
