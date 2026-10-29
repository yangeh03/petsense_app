#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robust rigid alignment of a UWB outline trajectory to a polygon map.

This is the arbitrary-polygon counterpart of ``uwb_rect_boundary_align.py``.
It keeps metric scale fixed by default and searches rotation + translation so
that boundary-walk UWB points fall near the known floorplan outline.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from uwb_map_geometry import load_map_model


TRANSFORM_FORMAT = "uwb_trajectory_transform_v1"
REPORT_FORMAT = "uwb_polygon_boundary_alignment_v1"


def load_xy_csv(path: Path, x_col: str, y_col: str, updated_only: bool) -> np.ndarray:
    xy: List[List[float]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if updated_only and str(row.get("pos_updated", "")).strip() not in {"1", "true", "True"}:
                continue
            if str(row.get("valid_fix", "1")).strip() in {"0", "false", "False"}:
                continue
            try:
                x = float(str(row.get(x_col, "")).strip())
                y = float(str(row.get(y_col, "")).strip())
            except ValueError:
                continue
            if math.isfinite(x) and math.isfinite(y):
                xy.append([x, y])
    if len(xy) < 10:
        raise ValueError("trajectory must contain at least 10 valid points")
    return np.asarray(xy, dtype=float)


def rolling_median_xy(points: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return points.copy()
    if window % 2 == 0:
        window += 1
    half = window // 2
    out = np.zeros_like(points)
    for i in range(len(points)):
        lo = max(0, i - half)
        hi = min(len(points), i + half + 1)
        out[i] = np.median(points[lo:hi], axis=0)
    return out


def remove_step_spikes(points: np.ndarray, max_step_m: float) -> Tuple[np.ndarray, np.ndarray]:
    if max_step_m <= 0 or len(points) < 3:
        return points.copy(), np.arange(len(points))
    keep = [0]
    last = points[0]
    for i in range(1, len(points)):
        step = float(np.linalg.norm(points[i] - last))
        if step <= max_step_m:
            keep.append(i)
            last = points[i]
    idx = np.asarray(keep, dtype=int)
    return points[idx], idx


def drop_near_duplicates(points: np.ndarray, min_step_m: float) -> np.ndarray:
    if min_step_m <= 0 or len(points) < 2:
        return points.copy()
    keep = [0]
    last = points[0]
    for i in range(1, len(points)):
        if float(np.linalg.norm(points[i] - last)) >= min_step_m:
            keep.append(i)
            last = points[i]
    return points[np.asarray(keep, dtype=int)]


def preprocess(points: np.ndarray, median_window: int, max_step_m: float, min_step_m: float) -> np.ndarray:
    smooth = rolling_median_xy(points, median_window)
    clean, _ = remove_step_spikes(smooth, max_step_m)
    return drop_near_duplicates(clean, min_step_m)


def rotation_matrix(theta_rad: float) -> np.ndarray:
    c = math.cos(theta_rad)
    s = math.sin(theta_rad)
    return np.asarray([[c, -s], [s, c]], dtype=float)


def apply_transform(points: np.ndarray, scale: float, rot: np.ndarray, trans: np.ndarray) -> np.ndarray:
    return scale * (points @ rot.T) + trans


def source_mirror_matrix(mirror_x: bool, mirror_y: bool) -> np.ndarray:
    sx = -1.0 if mirror_x else 1.0
    sy = -1.0 if mirror_y else 1.0
    return np.asarray([[sx, 0.0], [0.0, sy]], dtype=float)


def polygon_edges(poly: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    a = poly
    b = np.roll(poly, -1, axis=0)
    v = b - a
    length = np.linalg.norm(v, axis=1)
    return a, v, length


def point_polygon_distances(points: np.ndarray, poly: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    starts, vecs, lengths = polygon_edges(poly)
    best_dist = np.full(len(points), np.inf, dtype=float)
    best_edge = np.zeros(len(points), dtype=int)
    best_u = np.zeros(len(points), dtype=float)

    for edge_idx, (a, v, length) in enumerate(zip(starts, vecs, lengths)):
        if length < 1e-12:
            proj = np.repeat(a[np.newaxis, :], len(points), axis=0)
            u = np.zeros(len(points), dtype=float)
        else:
            ap = points - a
            u = np.clip((ap @ v) / (length * length), 0.0, 1.0)
            proj = a + u[:, np.newaxis] * v
        dist = np.linalg.norm(points - proj, axis=1)
        mask = dist < best_dist
        best_dist[mask] = dist[mask]
        best_edge[mask] = edge_idx
        best_u[mask] = u[mask]
    return best_dist, best_edge, best_u


def points_in_polygon(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    x = points[:, 0]
    y = points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    x0 = poly[:, 0]
    y0 = poly[:, 1]
    x1 = np.roll(x0, -1)
    y1 = np.roll(y0, -1)
    for xi, yi, xj, yj in zip(x0, y0, x1, y1):
        crosses = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi + 1e-18) + xi)
        inside ^= crosses
    return inside


def edge_coverage(
    edge_idx: np.ndarray,
    edge_u: np.ndarray,
    inlier: np.ndarray,
    edge_lengths: np.ndarray,
    bin_m: float,
) -> Tuple[float, int, List[Dict[str, Any]]]:
    details: List[Dict[str, Any]] = []
    total_perimeter = float(np.sum(edge_lengths))
    covered_total = 0.0
    covered_edges = 0
    for i, length in enumerate(edge_lengths):
        mask = inlier & (edge_idx == i)
        count = int(np.count_nonzero(mask))
        if count == 0 or length < 1e-12:
            covered_m = 0.0
            ratio = 0.0
        else:
            bins = max(1, int(math.ceil(float(length) / max(bin_m, 1e-6))))
            hit = np.zeros(bins, dtype=bool)
            idx = np.clip((edge_u[mask] * bins).astype(int), 0, bins - 1)
            hit[idx] = True
            covered_m = float(np.count_nonzero(hit) / bins * length)
            ratio = float(covered_m / length)
        if ratio >= 0.2:
            covered_edges += 1
        covered_total += covered_m
        details.append(
            {
                "edge_index": i,
                "length_m": float(length),
                "near_point_count": count,
                "covered_m": covered_m,
                "coverage_ratio": ratio,
            }
        )
    return float(covered_total / max(total_perimeter, 1e-9)), covered_edges, details


def score_candidate(
    mapped: np.ndarray,
    poly: np.ndarray,
    edge_tol: float,
    outside_margin: float,
    coverage_bin_m: float,
) -> Dict[str, Any]:
    edge_lengths = polygon_edges(poly)[2]
    bdist, edge_idx, edge_u = point_polygon_distances(mapped, poly)
    inside = points_in_polygon(mapped, poly)
    inlier = bdist <= edge_tol
    outside_far = (~inside) & (bdist > outside_margin)

    inlier_count = int(np.count_nonzero(inlier))
    inlier_ratio = float(inlier_count / max(len(mapped), 1))
    outside_far_ratio = float(np.count_nonzero(outside_far) / max(len(mapped), 1))

    if inlier_count:
        median_edge = float(np.median(bdist[inlier]))
        p90_edge = float(np.percentile(bdist[inlier], 90))
    else:
        median_edge = float("inf")
        p90_edge = float("inf")

    coverage_ratio, covered_edges, coverage_details = edge_coverage(
        edge_idx, edge_u, inlier, edge_lengths, coverage_bin_m
    )

    score = coverage_ratio * 10.0 + inlier_ratio * 3.0 + covered_edges * 0.18 - outside_far_ratio * 2.0
    if math.isfinite(median_edge):
        score -= median_edge / max(edge_tol, 1e-9)
        score -= 0.25 * p90_edge / max(edge_tol, 1e-9)
    else:
        score -= 100.0

    return {
        "score": float(score),
        "inlier": inlier,
        "edge_index": edge_idx,
        "edge_u": edge_u,
        "edge_coverage": coverage_details,
        "boundary_coverage_ratio": coverage_ratio,
        "covered_edges": covered_edges,
        "inlier_count": inlier_count,
        "inlier_ratio": inlier_ratio,
        "outside_far_ratio": outside_far_ratio,
        "median_edge_error_m": median_edge,
        "p90_edge_error_m": p90_edge,
    }


def translation_candidates(rotated: np.ndarray, poly: np.ndarray) -> Tuple[List[float], List[float]]:
    mn = poly.min(axis=0)
    mx = poly.max(axis=0)
    center = (mn + mx) / 2.0
    px = rotated[:, 0]
    py = rotated[:, 1]
    tx = [
        center[0] - float(np.median(px)),
        mn[0] - float(np.percentile(px, 2)),
        mn[0] - float(np.percentile(px, 8)),
        mx[0] - float(np.percentile(px, 92)),
        mx[0] - float(np.percentile(px, 98)),
    ]
    ty = [
        center[1] - float(np.median(py)),
        mn[1] - float(np.percentile(py, 2)),
        mn[1] - float(np.percentile(py, 8)),
        mx[1] - float(np.percentile(py, 92)),
        mx[1] - float(np.percentile(py, 98)),
    ]
    return sorted(set(round(v, 6) for v in tx)), sorted(set(round(v, 6) for v in ty))


def refine_translation(
    points: np.ndarray,
    poly: np.ndarray,
    rot: np.ndarray,
    scale: float,
    trans0: np.ndarray,
    edge_tol: float,
    outside_margin: float,
    coverage_bin_m: float,
    radius_m: float,
    step_m: float,
) -> Dict[str, Any]:
    base = scale * (points @ rot.T)
    best: Optional[Dict[str, Any]] = None
    offsets = np.arange(-radius_m, radius_m + 1e-9, step_m)
    for dx in offsets:
        for dy in offsets:
            trans = trans0 + np.asarray([dx, dy], dtype=float)
            mapped = base + trans
            sc = score_candidate(mapped, poly, edge_tol, outside_margin, coverage_bin_m)
            cand = {**sc, "translation_m": trans, "mapped": mapped}
            if best is None or cand["score"] > best["score"]:
                best = cand
    assert best is not None
    return best


def search_alignment(
    points: np.ndarray,
    poly: np.ndarray,
    edge_tol: float,
    outside_margin: float,
    angle_step_deg: float,
    allow_scale: bool,
    coverage_bin_m: float,
) -> Dict[str, Any]:
    scales = [1.0]
    if allow_scale:
        scales = [float(v) for v in np.linspace(0.9, 1.1, 9)]
    best: Optional[Dict[str, Any]] = None

    def eval_angle(theta_deg: float) -> None:
        nonlocal best
        theta = math.radians(theta_deg)
        rot = rotation_matrix(theta)
        for scale in scales:
            rotated = scale * (points @ rot.T)
            txs, tys = translation_candidates(rotated, poly)
            for tx in txs:
                for ty in tys:
                    trans = np.asarray([tx, ty], dtype=float)
                    mapped = rotated + trans
                    sc = score_candidate(mapped, poly, edge_tol, outside_margin, coverage_bin_m)
                    cand = {
                        **sc,
                        "theta_deg": float(theta_deg % 360.0),
                        "scale": float(scale),
                        "rotation_matrix": rot,
                        "translation_m": trans,
                        "mapped": mapped,
                    }
                    if best is None or cand["score"] > best["score"]:
                        best = cand

    for theta_deg in np.arange(0.0, 360.0, angle_step_deg):
        eval_angle(float(theta_deg))

    assert best is not None
    coarse_theta = float(best["theta_deg"])
    refine_step = max(angle_step_deg / 8.0, 0.1)
    for theta_deg in np.arange(coarse_theta - angle_step_deg * 2, coarse_theta + angle_step_deg * 2 + 1e-9, refine_step):
        eval_angle(float(theta_deg))

    assert best is not None
    trans_refined = refine_translation(
        points,
        poly,
        best["rotation_matrix"],
        float(best["scale"]),
        best["translation_m"],
        edge_tol,
        outside_margin,
        coverage_bin_m,
        radius_m=0.35,
        step_m=0.025,
    )
    best = {**best, **trans_refined}
    return best


def evenly_subsample(points: np.ndarray, max_points: int) -> np.ndarray:
    if max_points <= 0 or len(points) <= max_points:
        return points
    idx = np.linspace(0, len(points) - 1, max_points).round().astype(int)
    return points[np.unique(idx)]


def write_inlier_csv(path: Path, clean: np.ndarray, mapped: np.ndarray, best: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["clean_x_m", "clean_y_m", "map_x_m", "map_y_m", "nearest_edge", "edge_u", "is_boundary_inlier"])
        for c, m, e, u, ok in zip(clean, best["mapped"], best["edge_index"], best["edge_u"], best["inlier"]):
            w.writerow([c[0], c[1], m[0], m[1], int(e), float(u), int(bool(ok))])


def plot_preview(path: Path, raw: np.ndarray, clean: np.ndarray, poly: np.ndarray, best: Dict[str, Any]) -> None:
    mapped = best["mapped"]
    inlier = best["inlier"]
    closed = np.vstack([poly, poly[:1]])
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), constrained_layout=True)
    axes[0].plot(raw[:, 0], raw[:, 1], color="0.75", linewidth=1.0, label="raw trail")
    axes[0].scatter(clean[:, 0], clean[:, 1], s=8, c="tab:blue", label="clean trail")
    axes[0].scatter(clean[inlier, 0], clean[inlier, 1], s=10, c="tab:green", label="boundary inliers")
    axes[0].set_title("UWB trajectory")
    axes[0].set_aspect("equal", adjustable="box")
    axes[0].grid(True, linestyle="--", alpha=0.4)
    axes[0].legend(loc="best", fontsize=8)

    axes[1].fill(closed[:, 0], closed[:, 1], color="#8d91b5", alpha=0.35, label="floorplan")
    axes[1].plot(closed[:, 0], closed[:, 1], "k-", linewidth=1.5)
    axes[1].plot(mapped[:, 0], mapped[:, 1], color="tab:blue", linewidth=1.0, alpha=0.75, label="mapped trail")
    axes[1].scatter(mapped[inlier, 0], mapped[inlier, 1], s=10, c="tab:green", label="boundary inliers")
    axes[1].set_title(
        f"rotation={best['theta_deg']:.2f} deg, "
        f"coverage={best['boundary_coverage_ratio']:.2f}, "
        f"median err={best['median_edge_error_m']:.2f} m"
    )
    axes[1].set_aspect("equal", adjustable="box")
    axes[1].grid(True, linestyle="--", alpha=0.4)
    axes[1].legend(loc="best", fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description="Robust rigid alignment of UWB outline trajectory to a polygon floorplan")
    ap.add_argument("-i", "--input", required=True, type=Path, help="trajectory CSV")
    ap.add_argument("-m", "--map", required=True, type=Path, help="polygon map JSON")
    ap.add_argument("-o", "--output", required=True, type=Path, help="output transform JSON")
    ap.add_argument("--report", type=Path, default=None, help="output alignment report JSON")
    ap.add_argument("--preview-png", type=Path, default=None, help="output preview image")
    ap.add_argument("--inlier-csv", type=Path, default=None, help="output matched/inlier points CSV")
    ap.add_argument("--x-col", default="x_m")
    ap.add_argument("--y-col", default="y_m")
    ap.add_argument("--all-points", action="store_true", help="use all valid rows instead of only pos_updated=1")
    ap.add_argument("--median-window", type=int, default=5)
    ap.add_argument("--max-step", type=float, default=0.75)
    ap.add_argument("--min-step", type=float, default=0.015)
    ap.add_argument("--edge-tol", type=float, default=0.32, help="boundary inlier tolerance in meters")
    ap.add_argument("--outside-margin", type=float, default=0.55)
    ap.add_argument("--coverage-bin", type=float, default=0.20)
    ap.add_argument("--angle-step", type=float, default=2.0)
    ap.add_argument("--max-search-points", type=int, default=450, help="subsample clean points for coarse search")
    ap.add_argument("--mirror-uwb-x", action="store_true", help="mirror source UWB x before matching")
    ap.add_argument("--mirror-uwb-y", action="store_true", help="mirror source UWB y before matching")
    ap.add_argument("--allow-scale", action="store_true", help="diagnostic only: search small scale changes")
    args = ap.parse_args()

    raw = load_xy_csv(args.input, args.x_col, args.y_col, updated_only=not args.all_points)
    clean = preprocess(raw, args.median_window, args.max_step, args.min_step)
    if len(clean) < 10:
        raise ValueError("too few clean points after preprocessing")

    poly, closed = load_map_model(args.map)
    if not closed:
        raise ValueError("polygon boundary alignment requires a polygon map")

    source_mirror = source_mirror_matrix(args.mirror_uwb_x, args.mirror_uwb_y)
    raw_source = raw @ source_mirror.T
    clean_source = clean @ source_mirror.T

    search_points = evenly_subsample(clean_source, args.max_search_points)
    best_search = search_alignment(
        search_points,
        poly,
        args.edge_tol,
        args.outside_margin,
        args.angle_step,
        args.allow_scale,
        args.coverage_bin,
    )
    mapped_full = apply_transform(
        clean_source,
        float(best_search["scale"]),
        best_search["rotation_matrix"],
        best_search["translation_m"],
    )
    full_score = score_candidate(mapped_full, poly, args.edge_tol, args.outside_margin, args.coverage_bin)
    best = {**best_search, **full_score, "mapped": mapped_full}
    output_matrix = best["rotation_matrix"] @ source_mirror

    transform = {
        "format": TRANSFORM_FORMAT,
        "scale": float(best["scale"]),
        "rotation_matrix": output_matrix.tolist(),
        "rotation_deg": float(best["theta_deg"]),
        "translation_m": best["translation_m"].tolist(),
        "source": "uwb_polygon_boundary_align",
        "source_traj": str(args.input),
        "map_model": str(args.map),
        "scale_locked": not bool(args.allow_scale),
        "source_mirror_x": bool(args.mirror_uwb_x),
        "source_mirror_y": bool(args.mirror_uwb_y),
        "source_mirror_matrix": source_mirror.tolist(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(transform, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "format": REPORT_FORMAT,
        "input_csv": str(args.input),
        "map_model": str(args.map),
        "raw_points": int(len(raw)),
        "clean_points": int(len(clean)),
        "search_points": int(len(search_points)),
        "scale": float(best["scale"]),
        "scale_locked": not bool(args.allow_scale),
        "source_mirror_x": bool(args.mirror_uwb_x),
        "source_mirror_y": bool(args.mirror_uwb_y),
        "rotation_deg": float(best["theta_deg"]),
        "translation_m": best["translation_m"].tolist(),
        "score": float(best["score"]),
        "inlier_count": int(best["inlier_count"]),
        "inlier_ratio": float(best["inlier_ratio"]),
        "outside_far_ratio": float(best["outside_far_ratio"]),
        "median_edge_error_m": float(best["median_edge_error_m"]),
        "p90_edge_error_m": float(best["p90_edge_error_m"]),
        "boundary_coverage_ratio": float(best["boundary_coverage_ratio"]),
        "covered_edges": int(best["covered_edges"]),
        "edge_coverage": best["edge_coverage"],
        "parameters": {
            "updated_only": not bool(args.all_points),
            "median_window": int(args.median_window),
            "max_step_m": float(args.max_step),
            "min_step_m": float(args.min_step),
            "edge_tol_m": float(args.edge_tol),
            "outside_margin_m": float(args.outside_margin),
            "coverage_bin_m": float(args.coverage_bin),
            "angle_step_deg": float(args.angle_step),
            "max_search_points": int(args.max_search_points),
            "mirror_uwb_x": bool(args.mirror_uwb_x),
            "mirror_uwb_y": bool(args.mirror_uwb_y),
        },
    }

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.inlier_csv is not None:
        write_inlier_csv(args.inlier_csv, clean_source, best["mapped"], best)
    if args.preview_png is not None:
        plot_preview(args.preview_png, raw_source, clean_source, poly, best)

    print(f"wrote transform={args.output}")
    if args.report is not None:
        print(f"wrote report={args.report}")
    if args.preview_png is not None:
        print(f"wrote preview={args.preview_png}")
    print(f"rotation_deg={best['theta_deg']:.3f}")
    tx, ty = best["translation_m"]
    print(f"translation_m=[{tx:.3f}, {ty:.3f}]")
    print(f"scale={best['scale']:.4f} scale_locked={not bool(args.allow_scale)}")
    print(
        "inliers="
        f"{best['inlier_count']}/{len(clean)} "
        f"median_edge_error={best['median_edge_error_m']:.3f}m "
        f"coverage={best['boundary_coverage_ratio']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
