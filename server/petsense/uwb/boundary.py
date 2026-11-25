"""Constrain display coordinates to a map's reviewed point-cloud polygon."""
import math


def nearest(point, polygon):
    candidates = []
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        t = max(0., min(1., ((point[0]-a[0])*dx + (point[1]-a[1])*dy) / length)) if length else 0.
        candidates.append((a[0] + t*dx, a[1] + t*dy))
    return min(candidates, key=lambda p: math.dist(point, p))


def contains(point, polygon):
    if math.dist(point, nearest(point, polygon)) <= 1e-9:
        return True
    x, y = point
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def constrain(target, polygon, previous=None):
    """Stop at the first exit along motion, including concave notches.

    An initial fix has no motion history, so use its nearest boundary point.
    Never feed the constrained point back into the measurement filter.
    """
    if previous is None or not contains(previous, polygon):
        return target if contains(target, polygon) else nearest(target, polygon)
    dx, dy = target[0]-previous[0], target[1]-previous[1]
    cuts = [0., 1.]
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        ex, ey = b[0]-a[0], b[1]-a[1]
        den = dx*ey-dy*ex
        if abs(den) < 1e-12:
            continue
        ax, ay = a[0]-previous[0], a[1]-previous[1]
        t, u = (ax*ey-ay*ex)/den, (ax*dy-ay*dx)/den
        if 0 <= t <= 1 and -1e-9 <= u <= 1+1e-9:
            cuts.append(t)
    cuts = sorted(set(cuts))
    for start, end in zip(cuts, cuts[1:]):
        mid = (start+end)/2
        if not contains((previous[0]+mid*dx, previous[1]+mid*dy), polygon):
            return (previous[0]+start*dx, previous[1]+start*dy)
    return target
