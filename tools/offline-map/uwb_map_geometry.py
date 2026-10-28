#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
边界简化、弧长重采样、2D 相似变换（Umeyama）及空间模型文件读写。

**空间模型 JSON 格式（uwb_map_model v1）**

- 文件为 UTF-8 JSON，顶层含 ``"format": "uwb_map_model_v1"``。
- ``kind``: ``"polygon"``（闭合多边形，首尾可不重复，读取时会闭合）或 ``"polyline"``（开放折线）。
- ``coordinates``: ``[[x_m, y_m], ...]``，单位米，坐标系为**已知地图/房间**平面系。

示例::

    {
      "format": "uwb_map_model_v1",
      "kind": "polygon",
      "name": "demo_room",
      "coordinates": [[0, 0], [5, 0], [5, 4], [0, 4]]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# --- Ramer–Douglas–Peucker ---


def rdp_simplify(points: Sequence[Tuple[float, float]], epsilon_m: float) -> List[Tuple[float, float]]:
    """二维折线抽稀；epsilon_m 越大点越少。"""
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n < 3:
        return [tuple(p) for p in pts]
    eps = max(float(epsilon_m), 0.0)

    def rdp_range(i0: int, i1: int) -> List[int]:
        """保留折线段 [i0, i1]（闭区间）顶点下标，按沿弧顺序。"""
        if i1 - i0 <= 1:
            return [i0, i1]
        line = pts[i1] - pts[i0]
        ln = np.linalg.norm(line)
        mid = pts[i0 + 1 : i1]
        if mid.size == 0:
            return [i0, i1]
        if ln < 1e-12:
            d = np.linalg.norm(mid - pts[i0], axis=1)
        else:
            v = line / ln
            ap = mid - pts[i0]
            d = np.linalg.norm(ap - (ap @ v)[:, np.newaxis] * v, axis=1)
        k = int(np.argmax(d))
        if d[k] <= eps:
            return [i0, i1]
        im = i0 + 1 + k
        left = rdp_range(i0, im)
        right = rdp_range(im, i1)
        return left[:-1] + right

    order = rdp_range(0, n - 1)
    return [tuple(pts[i]) for i in order]


# --- Convex hull (Andrew monotone chain) ---


def convex_hull_2d(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """二维凸包，逆时针，无重复首点。"""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return list(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]
    return hull


# --- Arc-length resample ---


def polyline_arc_length(points: Sequence[Tuple[float, float]], closed: bool = False) -> float:
    pts = np.asarray(points, dtype=float)
    if len(pts) < 2:
        return 0.0
    segs = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    total = float(np.sum(segs))
    if closed and len(pts) >= 2:
        total += float(np.linalg.norm(pts[0] - pts[-1]))
    return total


def resample_polyline(
    points: Sequence[Tuple[float, float]],
    num_samples: int,
    closed: bool = False,
) -> np.ndarray:
    """
    按弧长均匀重采样为 num_samples 个点，形状 (num_samples, 2)。
    closed=True 时在首尾之间增加闭合边参与总长。
    """
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n == 0:
        return np.zeros((0, 2))
    if num_samples < 2:
        return np.repeat(pts[:1], num_samples or 1, axis=0)

    if n == 1:
        return np.repeat(pts, num_samples, axis=0)

    # Build vertex list including wrap for closed
    if closed:
        verts = np.vstack([pts, pts[:1]])
    else:
        verts = pts

    seg_len = np.linalg.norm(np.diff(verts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = cum[-1]
    if total < 1e-12:
        return np.repeat(pts[:1], num_samples, axis=0)

    targets = np.linspace(0.0, total, num_samples, endpoint=(not closed))
    out = np.zeros((num_samples, 2))
    j = 0
    for i, t in enumerate(targets):
        while j + 1 < len(cum) and cum[j + 1] < t - 1e-12:
            j += 1
        j = min(j, len(verts) - 2)
        t0, t1 = cum[j], cum[j + 1]
        if t1 - t0 < 1e-12:
            out[i] = verts[j]
        else:
            u = (t - t0) / (t1 - t0)
            out[i] = verts[j] * (1 - u) + verts[j + 1] * u
    return out


# --- Umeyama similarity (2D) ---


def umeyama_similarity_2d(src: np.ndarray, dst: np.ndarray) -> Tuple[float, np.ndarray, np.ndarray]:
    """
    src, dst: (N, 2)，求 c, R, t 使 dst ≈ c * R @ src + t（行向量形式：dst_i ≈ c * R @ src_i + t）。
    """
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2:
        raise ValueError("src/dst 须为 (N,2) 且形状相同")
    n = src.shape[0]
    mu_s = src.mean(axis=0)
    mu_d = dst.mean(axis=0)
    X = src - mu_s
    Y = dst - mu_d
    sigma_x2 = np.sum(X * X) / n
    if sigma_x2 < 1e-18:
        return 1.0, np.eye(2), mu_d - mu_s

    K = (Y.T @ X) / n
    U, D, Vt = np.linalg.svd(K)
    S = np.eye(2)
    if np.linalg.det(U @ Vt) < 0:
        S[1, 1] = -1.0
    R = U @ S @ Vt
    c = np.trace(np.diag(D) @ S) / sigma_x2 if sigma_x2 > 1e-18 else 1.0
    t = mu_d - c * (R @ mu_s)
    return float(c), R, t


def apply_similarity_2d(points: np.ndarray, scale: float, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """points (N,2)，返回 (N,2)。"""
    return scale * (points @ R.T) + t


def rms_alignment_error(
    src: np.ndarray,
    dst: np.ndarray,
    scale: float,
    R: np.ndarray,
    t: np.ndarray,
) -> float:
    pred = apply_similarity_2d(src, scale, R, t)
    err = pred - dst
    return float(np.sqrt(np.mean(np.sum(err * err, axis=1))))


# --- Model IO ---


def load_map_model(path: Path) -> Tuple[np.ndarray, bool]:
    """
    返回 (points_Nx2, closed)。
    polygon → closed True；polyline → closed False。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    fmt = data.get("format")
    if fmt != "uwb_map_model_v1":
        raise ValueError(f"不支持的 format: {fmt!r}，需要 uwb_map_model_v1")

    kind = data.get("kind")
    coords = data.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError("coordinates 至少需要 2 个点")

    arr = np.asarray(coords, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError("coordinates 须为 [[x,y], ...]")

    if kind == "polygon":
        # 闭合：若首尾不同则视为自动闭合
        return arr, True
    if kind == "polyline":
        return arr, False
    raise ValueError(f"不支持的 kind: {kind!r}，使用 polygon 或 polyline")


def save_map_model(
    path: Path,
    coordinates: Sequence[Tuple[float, float]],
    kind: str,
    name: str = "",
) -> None:
    obj: Dict[str, Any] = {
        "format": "uwb_map_model_v1",
        "kind": kind,
        "coordinates": [[float(x), float(y)] for x, y in coordinates],
    }
    if name:
        obj["name"] = name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# --- Registration ---


def register_trajectory_to_model(
    traj_xy: np.ndarray,
    model_xy: np.ndarray,
    model_closed: bool,
    num_samples: int = 128,
    try_reflections: bool = True,
) -> Dict[str, Any]:
    """
    将轨迹点（锚点坐标系）与地图折线/多边形（地图坐标系）对齐。
    对轨迹与模型均按弧长重采样 num_samples 点后做 Umeyama；遍历 x/y 独立翻转组合取 RMS 最小者。
    """
    if traj_xy.shape[0] < 3:
        raise ValueError("轨迹点过少（至少 3 个）")

    traj_list = [(float(traj_xy[i, 0]), float(traj_xy[i, 1])) for i in range(len(traj_xy))]
    traj_rs = resample_polyline(traj_list, num_samples, closed=False)

    m_list = [(float(model_xy[i, 0]), float(model_xy[i, 1])) for i in range(len(model_xy))]
    mod_rs = resample_polyline(m_list, num_samples, closed=model_closed)

    best: Optional[Dict[str, Any]] = None

    flips = [(1, 1), (1, -1), (-1, 1), (-1, -1)] if try_reflections else [(1, 1)]

    for sx, sy in flips:
        X = traj_rs * np.array([sx, sy], dtype=float)
        Y = mod_rs
        c, R, t = umeyama_similarity_2d(X, Y)
        rms = rms_alignment_error(X, Y, c, R, t)
        cand = {
            "scale": c,
            "R": R,
            "t": t,
            "rms_m": rms,
            "flip_x": sx < 0,
            "flip_y": sy < 0,
            "num_samples": num_samples,
        }
        if best is None or rms < best["rms_m"]:
            best = cand

    assert best is not None
    return best


def transform_trajectory_points(traj_xy: np.ndarray, reg: Dict[str, Any]) -> np.ndarray:
    """将轨迹从锚点系变换到地图系（使用 register_trajectory_to_model 返回的参数字典）。"""
    sx = -1.0 if reg.get("flip_x") else 1.0
    sy = -1.0 if reg.get("flip_y") else 1.0
    X = traj_xy * np.array([sx, sy], dtype=float)
    return apply_similarity_2d(X, reg["scale"], reg["R"], reg["t"])
