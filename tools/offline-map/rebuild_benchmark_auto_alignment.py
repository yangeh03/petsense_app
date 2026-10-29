#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark automatic UWB registrations against a held-out manual transform.

The manual transform is used only for evaluation, never for the automatic
objectives. All automatic methods operate on the raw UWB trajectory and the
original 0.08--1.00 m obstacle grid.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from scipy import ndimage, optimize


def load_raw_trail(path: Path) -> np.ndarray:
    points = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("valid_fix", "1")).strip() in {"0", "false", "False"}:
                continue
            try:
                point = [float(row.get("raw_x_m", row["x_m"])), float(row.get("raw_y_m", row["y_m"]))]
            except (KeyError, TypeError, ValueError):
                continue
            if np.isfinite(point).all():
                points.append(point)
    if len(points) < 2:
        raise ValueError("trajectory has fewer than two valid raw points")
    return np.asarray(points, dtype=float)


def load_transform(path: Path) -> Tuple[np.ndarray, np.ndarray, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    matrix = np.asarray(data["rotation_matrix"], dtype=float)
    translation = np.asarray(data["translation_m"], dtype=float)
    scale = float(data.get("scale", 1.0))
    if matrix.shape != (2, 2) or translation.shape != (2,):
        raise ValueError(f"invalid transform file: {path}")
    return matrix, translation, scale


def apply(points: np.ndarray, matrix: np.ndarray, translation: np.ndarray, scale: float) -> np.ndarray:
    return scale * (points @ matrix.T) + translation


def rot(theta: float) -> np.ndarray:
    return np.asarray([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])


MIRRORS: Dict[str, np.ndarray] = {
    "none": np.asarray([[1.0, 0.0], [0.0, 1.0]]),
    "x": np.asarray([[-1.0, 0.0], [0.0, 1.0]]),
    "y": np.asarray([[1.0, 0.0], [0.0, -1.0]]),
    "xy": np.asarray([[-1.0, 0.0], [0.0, -1.0]]),
}


class GridDistanceObjective:
    def __init__(self, points: np.ndarray, boundary_distance_m: np.ndarray, low: np.ndarray, cell: float, model_per_m: float) -> None:
        self.points = points
        self.distance = boundary_distance_m
        self.low = low
        self.cell = cell
        self.model_per_m = model_per_m
        self.height, self.width = boundary_distance_m.shape

    def sampled_distances(self, mapped_m: np.ndarray) -> np.ndarray:
        recon = mapped_m * self.model_per_m
        col = (recon[:, 0] - self.low[0]) / self.cell - 0.5
        row = (recon[:, 1] - self.low[1]) / self.cell - 0.5
        outside_x = np.maximum(0.0, np.maximum(-col, col - (self.width - 1)))
        outside_y = np.maximum(0.0, np.maximum(-row, row - (self.height - 1)))
        outside_m = np.hypot(outside_x, outside_y) * (self.cell / self.model_per_m)
        sampled = ndimage.map_coordinates(self.distance, [row, col], order=1, mode="nearest")
        return sampled + outside_m + (outside_m > 0.0) * 0.35

    def score(self, params: np.ndarray, mirror: np.ndarray, allow_scale: bool) -> float:
        theta, tx, ty = params[:3]
        scale = float(params[3]) if allow_scale else 1.0
        mapped = apply(self.points, rot(theta) @ mirror, np.asarray([tx, ty]), scale)
        distances = self.sampled_distances(mapped)
        # Robustly reward a large fraction of trajectory near structural edges,
        # while capping long return/drift sections so they cannot dominate.
        robust = float(np.mean(np.minimum(distances, 0.30)))
        scale_penalty = 0.04 * abs(scale - 1.0)
        return robust + scale_penalty


def transform_from_params(params: np.ndarray, mirror: np.ndarray, allow_scale: bool) -> Tuple[np.ndarray, np.ndarray, float]:
    theta, tx, ty = params[:3]
    return rot(theta) @ mirror, np.asarray([tx, ty]), float(params[3]) if allow_scale else 1.0


def transform_metrics(points: np.ndarray, candidate: Tuple[np.ndarray, np.ndarray, float], ground_truth: Tuple[np.ndarray, np.ndarray, float]) -> dict:
    candidate_mapped = apply(points, *candidate)
    gt_mapped = apply(points, *ground_truth)
    displacement = np.linalg.norm(candidate_mapped - gt_mapped, axis=1)
    return {
        "median_pose_displacement_m": float(np.median(displacement)),
        "p90_pose_displacement_m": float(np.quantile(displacement, 0.90)),
        "mean_pose_displacement_m": float(np.mean(displacement)),
    }


def write_transform(path: Path, candidate: Tuple[np.ndarray, np.ndarray, float], source: str) -> None:
    matrix, translation, scale = candidate
    path.write_text(json.dumps({
        "format": "uwb_trajectory_transform_v1",
        "scale": scale,
        "rotation_matrix": matrix.tolist(),
        "rotation_deg": math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])),
        "translation_m": translation.tolist(),
        "source": source,
        "scale_locked": scale == 1.0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark automatic obstacle-grid alignment against a manual ground truth")
    ap.add_argument("--grid", required=True, type=Path)
    ap.add_argument("--floor-transform", required=True, type=Path)
    ap.add_argument("--trail", required=True, type=Path)
    ap.add_argument("--ground-truth", required=True, type=Path)
    ap.add_argument("--polygon-initial", required=True, type=Path, help="existing X-mirror polygon alignment")
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--search-points", type=int, default=450)
    ap.add_argument("--maxiter", type=int, default=45)
    ap.add_argument("--seed", type=int, default=20260720)
    args = ap.parse_args()

    print("[1/5] loading trajectory and obstacle grid", flush=True)
    raw = load_raw_trail(args.trail)
    keep = np.linspace(0, len(raw) - 1, min(args.search_points, len(raw))).round().astype(int)
    search = raw[keep]
    floor = json.loads(args.floor_transform.read_text(encoding="utf-8"))
    model_per_m = float(floor["scale_prior"])
    grid = np.load(args.grid)
    obstacle_key = "obstacle_for_navigation" if "obstacle_for_navigation" in grid.files else "occupied_raw"
    obstacles = np.asarray(grid[obstacle_key], dtype=bool)
    low = np.asarray(grid["low"], dtype=float)
    cell = float(np.asarray(grid["cell_reconstruction_units"]).item())
    boundary = ndimage.binary_dilation(obstacles, structure=np.ones((3, 3), dtype=bool)) ^ ndimage.binary_erosion(obstacles, structure=np.ones((3, 3), dtype=bool))
    boundary_distance_m = ndimage.distance_transform_edt(~boundary) * (cell / model_per_m)
    objective = GridDistanceObjective(search, boundary_distance_m, low, cell, model_per_m)
    gt = load_transform(args.ground_truth)
    polygon_initial = load_transform(args.polygon_initial)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    candidates: dict[str, Tuple[np.ndarray, np.ndarray, float]] = {"polygon_x_mirror": polygon_initial}
    details: dict[str, dict] = {
        "polygon_x_mirror": {"method": "existing polygon-boundary registration", "objective": None}
    }

    # Bounds cover the reconstructed room plus one metre of slack.
    high = low + np.asarray([obstacles.shape[1] * cell, obstacles.shape[0] * cell])
    bounds = [(-math.pi, math.pi), (low[0] / model_per_m - 4.5, high[0] / model_per_m + 4.5), (low[1] / model_per_m - 4.5, high[1] / model_per_m + 4.5)]

    print("[2/5] global distance-field search, fixed metric scale, four mirror hypotheses", flush=True)
    for index, (name, mirror) in enumerate(MIRRORS.items(), start=1):
        result = optimize.differential_evolution(
            lambda p: objective.score(p, mirror, allow_scale=False),
            bounds=bounds,
            maxiter=args.maxiter,
            popsize=8,
            seed=args.seed + index,
            polish=True,
            updating="immediate",
            workers=1,
        )
        key = f"grid_global_fixed_{name}"
        candidates[key] = transform_from_params(result.x, mirror, allow_scale=False)
        details[key] = {"method": "global distance-field search", "mirror": name, "objective": float(result.fun), "iterations": int(result.nit)}
        print(f"  {name}: objective={result.fun:.4f}, iterations={result.nit}", flush=True)

    print("[3/5] global distance-field search, X mirror with bounded scale", flush=True)
    scale_bounds = bounds + [(0.85, 1.15)]
    scale_result = optimize.differential_evolution(
        lambda p: objective.score(p, MIRRORS["x"], allow_scale=True),
        bounds=scale_bounds,
        maxiter=args.maxiter,
        popsize=8,
        seed=args.seed + 10,
        polish=True,
        updating="immediate",
        workers=1,
    )
    candidates["grid_global_x_scale"] = transform_from_params(scale_result.x, MIRRORS["x"], allow_scale=True)
    details["grid_global_x_scale"] = {"method": "global distance-field search with scale", "mirror": "x", "objective": float(scale_result.fun), "iterations": int(scale_result.nit)}

    print("[4/5] local distance-field refinement from polygon X-mirror result", flush=True)
    base_r, base_t, _ = polygon_initial
    theta0 = math.atan2((base_r @ MIRRORS["x"])[1, 0], (base_r @ MIRRORS["x"])[0, 0])
    local = optimize.minimize(
        lambda p: objective.score(p, MIRRORS["x"], allow_scale=False),
        np.asarray([theta0, base_t[0], base_t[1]]),
        method="Powell",
        bounds=bounds,
        options={"maxiter": 900, "xtol": 1e-4, "ftol": 1e-5},
    )
    candidates["grid_local_from_polygon_x"] = transform_from_params(local.x, MIRRORS["x"], allow_scale=False)
    details["grid_local_from_polygon_x"] = {"method": "Powell refinement from polygon X-mirror", "mirror": "x", "objective": float(local.fun), "iterations": int(local.nit)}

    print("[5/5] evaluating candidates only now against held-out manual alignment", flush=True)
    report_candidates = {}
    for name, candidate in candidates.items():
        matrix, translation, scale = candidate
        mirror = MIRRORS.get(details.get(name, {}).get("mirror", "x"), MIRRORS["x"])
        pure_rotation = matrix @ mirror
        automatic_score = objective.score(
            np.asarray([
                math.atan2(pure_rotation[1, 0], pure_rotation[0, 0]),
                translation[0], translation[1], scale,
            ]),
            mirror,
            allow_scale=True,
        )
        report_candidates[name] = {
            **details[name],
            "matrix": matrix.tolist(),
            "translation_m": translation.tolist(),
            "scale": scale,
            "held_out_ground_truth": transform_metrics(raw, candidate, gt),
            "distance_field_score_recomputed": float(automatic_score),
        }
        write_transform(args.output_dir / f"{name}.json", candidate, details[name]["method"])

    ranked = sorted(
        report_candidates,
        key=lambda key: report_candidates[key]["held_out_ground_truth"]["median_pose_displacement_m"],
    )
    report = {
        "format": "uwb_auto_alignment_benchmark_v1",
        "ground_truth_used_only_for_evaluation": str(args.ground_truth),
        "trajectory": str(args.trail),
        "grid": str(args.grid),
        "search_points": int(len(search)),
        "candidates": report_candidates,
        "ranked_by_ground_truth_median_displacement": ranked,
    }
    (args.output_dir / "benchmark_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    best_name = ranked[0]
    height, width = obstacles.shape
    high = low + np.asarray([width * cell, height * cell])
    extent = [low[0] / model_per_m, high[0] / model_per_m, low[1] / model_per_m, high[1] / model_per_m]
    fig, ax = plt.subplots(figsize=(12, 9))
    ax.imshow(obstacles.astype(np.uint8), origin="lower", extent=extent, cmap=ListedColormap(["#ffffff", "#282828"]), interpolation="nearest")
    ax.plot(apply(raw, *gt)[:, 0], apply(raw, *gt)[:, 1], color="#19a45b", linewidth=1.0, alpha=0.85, label="manual ground truth")
    ax.plot(apply(raw, *candidates[best_name])[:, 0], apply(raw, *candidates[best_name])[:, 1], color="#e67e22", linewidth=0.95, alpha=0.85, label=f"best auto: {best_name}")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Best automatic candidate against held-out manual ground truth")
    ax.grid(True, color="#b8b8b8", linewidth=0.55, alpha=0.7)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(args.output_dir / "best_auto_vs_manual.png", dpi=180)
    plt.close(fig)
    print(f"best_by_ground_truth={best_name}", flush=True)
    for name in ranked:
        metric = report_candidates[name]["held_out_ground_truth"]
        print(f"  {name}: median_pose_displacement={metric['median_pose_displacement_m']:.3f} m, p90={metric['p90_pose_displacement_m']:.3f} m", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
