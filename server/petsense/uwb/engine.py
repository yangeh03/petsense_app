"""Distance filtering, 2-D trilateration, map transform, and quality output."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Deque, Dict, Optional, Tuple

from .maps import EngineConfig as AppConfig
from .measurement import MeasurementFrame


Point = Tuple[float, float]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _lerp(start: float, end: float, amount: float) -> float:
    amount = _clamp(amount, 0.0, 1.0)
    return start * (1.0 - amount) + end * amount


def trilaterate_2d(p0: Point, p1: Point, p2: Point, d0: float, d1: float, d2: float) -> Optional[Point]:
    a11 = 2.0 * (p1[0] - p0[0])
    a12 = 2.0 * (p1[1] - p0[1])
    a21 = 2.0 * (p2[0] - p0[0])
    a22 = 2.0 * (p2[1] - p0[1])
    b1 = d0 * d0 - d1 * d1 - p0[0] ** 2 + p1[0] ** 2 - p0[1] ** 2 + p1[1] ** 2
    b2 = d0 * d0 - d2 * d2 - p0[0] ** 2 + p2[0] ** 2 - p0[1] ** 2 + p2[1] ** 2
    determinant = a11 * a22 - a12 * a21
    if abs(determinant) < 1e-9:
        return None
    return (
        (b1 * a22 - b2 * a12) / determinant,
        (a11 * b2 - a21 * b1) / determinant,
    )


@dataclass
class DistanceState:
    history: Deque[float]
    raw_m: Optional[float] = None
    filtered_m: Optional[float] = None
    updated_monotonic: float = 0.0


@dataclass
class PositionEngine:
    config: AppConfig
    tag_id: int
    distances: Dict[int, DistanceState] = field(init=False)
    raw_position: Optional[Point] = None
    position: Optional[Point] = None
    latest: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.distances = {
            index: DistanceState(deque(maxlen=self.config.filters.median_window))
            for index in self.config.anchor_indices
        }

    def update(self, frame: MeasurementFrame, now_monotonic: Optional[float] = None) -> Dict[str, Any]:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        for index in self.config.anchor_indices:
            value = frame.distances_m.get(index)
            if value is None or not math.isfinite(value):
                continue
            if value < self.config.min_distance_m or value > self.config.max_distance_m:
                continue
            state = self.distances[index]
            state.raw_m = value
            state.filtered_m = self._filter_distance(state, value)
            state.updated_monotonic = now

        self.latest = self._build_snapshot(frame, now)
        return self.latest

    def refresh_status(self, now_monotonic: Optional[float] = None) -> Dict[str, Any]:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        if not self.latest:
            return self.waiting_snapshot("waiting_for_data")
        if any(now - item.updated_monotonic > self.config.stale_after_seconds for item in self.distances.values()):
            refreshed = dict(self.latest)
            refreshed["status"] = "stale"
            refreshed["fresh"] = False
            refreshed["timestamp_ms"] = int(time.time() * 1000)
            self.latest = refreshed
        return self.latest

    def waiting_snapshot(self, reason: str) -> Dict[str, Any]:
        return {
            "type": "position",
            "schema_version": 1,
            "tag_id": self.tag_id,
            "timestamp_ms": int(time.time() * 1000),
            "status": "waiting",
            "fresh": False,
            "reason": reason,
            "position_uwb_m": None,
            "position_map_m": None,
            "distances_m": {},
            "quality": None,
        }

    def _filter_distance(self, state: DistanceState, new_value: float) -> float:
        settings = self.config.filters
        state.history.append(new_value)
        middle = float(median(state.history))
        if state.filtered_m is None:
            return middle
        delta = middle - state.filtered_m
        motion_ratio = abs(delta) / max(settings.dist_motion_band_m, 1e-6)
        alpha = _lerp(settings.dist_ema_static, settings.dist_ema_moving, motion_ratio)
        gate = _lerp(settings.dist_outlier_static_m, settings.dist_outlier_moving_m, motion_ratio)
        max_step = _lerp(settings.dist_max_step_static_m, settings.dist_max_step_moving_m, motion_ratio)
        if abs(delta) > gate:
            middle = state.filtered_m + _clamp(delta, -max_step, max_step)
        return state.filtered_m * (1.0 - alpha) + middle * alpha

    def _build_snapshot(self, frame: MeasurementFrame, now: float) -> Dict[str, Any]:
        values = [self.distances[index] for index in self.config.anchor_indices]
        fresh = all(
            item.filtered_m is not None and now - item.updated_monotonic <= self.config.stale_after_seconds
            for item in values
        )
        status = "ok" if fresh else "waiting"
        reason: Optional[str] = None
        residual_rms: Optional[float] = None
        inside_map: Optional[bool] = None

        if fresh:
            d0, d1, d2 = (float(item.filtered_m) for item in values)  # type: ignore[arg-type]
            fix = trilaterate_2d(*self.config.anchors_uwb_m, d0, d1, d2)
            if fix is None:
                status = "error"
                reason = "degenerate_anchor_geometry"
            else:
                self.raw_position = fix
                self.position = fix if self.position is None else self._smooth_position(self.position, fix)
                predicted = [
                    math.hypot(fix[0] - anchor[0], fix[1] - anchor[1])
                    for anchor in self.config.anchors_uwb_m
                ]
                residual_rms = math.sqrt(sum((got - expected) ** 2 for got, expected in zip(predicted, (d0, d1, d2))) / 3.0)
                map_position = self._map_point(self.position)
                inside_map = _point_in_polygon(map_position, self.config.map_polygon_m)
                if residual_rms > self.config.max_residual_m:
                    status = "low_quality"
                    reason = "range_residual_too_large"
        else:
            reason = "not_all_anchor_distances_are_fresh"

        map_position = self._map_point(self.position) if self.position is not None else None
        raw_map_position = self._map_point(self.raw_position) if self.raw_position is not None else None
        score = None
        if residual_rms is not None:
            score = round(_clamp(1.0 - residual_rms / self.config.max_residual_m, 0.0, 1.0), 3)
        return {
            "type": "position",
            "schema_version": 1,
            "tag_id": self.tag_id,
            "timestamp_ms": int(time.time() * 1000),
            "device_timer": frame.device_timer,
            "sequence": frame.sequence,
            "source": frame.source,
            "status": status,
            "fresh": fresh,
            "reason": reason,
            "position_uwb_m": _point_object(self.position),
            "raw_position_uwb_m": _point_object(self.raw_position),
            "position_map_m": _point_object(map_position),
            "raw_position_map_m": _point_object(raw_map_position),
            "distances_m": {
                f"A{index}": {
                    "raw": self.distances[index].raw_m,
                    "filtered": self.distances[index].filtered_m,
                    "age_ms": round(max(0.0, now - self.distances[index].updated_monotonic) * 1000),
                }
                for index in self.config.anchor_indices
            },
            "quality": {
                "score": score,
                "range_residual_rms_m": residual_rms,
                "inside_map": inside_map,
            }
            if residual_rms is not None
            else None,
        }

    def _smooth_position(self, previous: Point, raw: Point) -> Point:
        settings = self.config.filters
        movement = math.hypot(raw[0] - previous[0], raw[1] - previous[1])
        ratio = movement / max(settings.pos_motion_band_m, 1e-6)
        alpha = _lerp(settings.pos_smooth_static, settings.pos_smooth_moving, ratio)
        deadband = _lerp(settings.pos_deadband_static_m, settings.pos_deadband_moving_m, ratio)
        candidate = (
            previous[0] * (1.0 - alpha) + raw[0] * alpha,
            previous[1] * (1.0 - alpha) + raw[1] * alpha,
        )
        return candidate if math.hypot(candidate[0] - previous[0], candidate[1] - previous[1]) >= deadband else previous

    def _map_point(self, point: Point) -> Point:
        matrix = self.config.transform_matrix
        scale = self.config.transform_scale
        translation = self.config.transform_translation_m
        return (
            scale * (matrix[0][0] * point[0] + matrix[0][1] * point[1]) + translation[0],
            scale * (matrix[1][0] * point[0] + matrix[1][1] * point[1]) + translation[1],
        )


def _point_object(point: Optional[Point]) -> Optional[Dict[str, float]]:
    if point is None:
        return None
    return {"x": point[0], "y": point[1], "z": 0.0}


def _point_in_polygon(point: Point, polygon: Tuple[Point, ...]) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if _point_on_segment(point, previous, current):
            return True
        crosses = (y1 > y) != (y2 > y)
        if crosses:
            intersection_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < intersection_x:
                inside = not inside
        previous = current
    return inside


def _point_on_segment(point: Point, start: Point, end: Point, epsilon: float = 1e-9) -> bool:
    cross = (point[1] - start[1]) * (end[0] - start[0]) - (point[0] - start[0]) * (end[1] - start[1])
    if abs(cross) > epsilon:
        return False
    return (
        min(start[0], end[0]) - epsilon <= point[0] <= max(start[0], end[0]) + epsilon
        and min(start[1], end[1]) - epsilon <= point[1] <= max(start[1], end[1]) + epsilon
    )
