#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate a UWB-to-reconstruction transform against an obstacle grid."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from scipy import ndimage


def load_raw_trail(path: Path) -> np.ndarray:
    points: list[list[float]] = []
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
        raise ValueError("raw trajectory has fewer than two valid points")
    return np.asarray(points, dtype=float)


def load_transform(path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    matrix = np.asarray(data["rotation_matrix"], dtype=float)
    translation = np.asarray(data["translation_m"], dtype=float)
    scale = float(data.get("scale", 1.0))
    if matrix.shape != (2, 2) or translation.shape != (2,):
        raise ValueError(f"invalid transform: {path}")
    return matrix, translation, scale


def apply(points: np.ndarray, transform_path: Path) -> np.ndarray:
    matrix, translation, scale = load_transform(transform_path)
    return scale * (points @ matrix.T) + translation


def summarize(mapped_m: np.ndarray, obstacles: np.ndarray, indoor: np.ndarray, reachable: np.ndarray, low: np.ndarray, cell: float, model_per_m: float) -> dict:
    recon = mapped_m * model_per_m
    ix = np.floor((recon[:, 0] - low[0]) / cell).astype(int)
    iy = np.floor((recon[:, 1] - low[1]) / cell).astype(int)
    height, width = obstacles.shape
    in_grid = (ix >= 0) & (ix < width) & (iy >= 0) & (iy < height)
    obstacle_hit = np.zeros(len(mapped_m), dtype=bool)
    indoor_hit = np.zeros(len(mapped_m), dtype=bool)
    reachable_hit = np.zeros(len(mapped_m), dtype=bool)
    obstacle_hit[in_grid] = obstacles[iy[in_grid], ix[in_grid]]
    indoor_hit[in_grid] = indoor[iy[in_grid], ix[in_grid]]
    reachable_hit[in_grid] = reachable[iy[in_grid], ix[in_grid]]

    # One-cell-thick band on both sides of each obstacle boundary.
    boundary = ndimage.binary_dilation(obstacles, structure=np.ones((3, 3), dtype=bool)) ^ ndimage.binary_erosion(obstacles, structure=np.ones((3, 3), dtype=bool))
    distance_grid_m = ndimage.distance_transform_edt(~boundary) * (cell / model_per_m)
    distances = np.full(len(mapped_m), np.nan, dtype=float)
    distances[in_grid] = distance_grid_m[iy[in_grid], ix[in_grid]]
    finite = distances[np.isfinite(distances)]
    return {
        "points": int(len(mapped_m)),
        "in_grid_ratio": float(in_grid.mean()),
        "inside_indoor_ratio": float(indoor_hit.mean()),
        "inside_reachable_ratio": float(reachable_hit.mean()),
        "inside_obstacle_ratio": float(obstacle_hit.mean()),
        "median_boundary_distance_m": float(np.median(finite)) if len(finite) else None,
        "p90_boundary_distance_m": float(np.quantile(finite, 0.90)) if len(finite) else None,
        "within_0p05m_ratio": float(np.mean(finite <= 0.05)) if len(finite) else None,
        "within_0p10m_ratio": float(np.mean(finite <= 0.10)) if len(finite) else None,
        "within_0p20m_ratio": float(np.mean(finite <= 0.20)) if len(finite) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate raw UWB alignment against the original obstacle grid")
    ap.add_argument("--grid", required=True, type=Path)
    ap.add_argument("--floor-transform", required=True, type=Path)
    ap.add_argument("--trail", required=True, type=Path)
    ap.add_argument("--transform", required=True, type=Path, help="manual/final transform")
    ap.add_argument("--baseline-transform", type=Path, default=None, help="optional automatic initial transform")
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--preview", type=Path, default=None)
    args = ap.parse_args()

    raw = load_raw_trail(args.trail)
    floor = json.loads(args.floor_transform.read_text(encoding="utf-8"))
    model_per_m = float(floor["scale_prior"])
    grid = np.load(args.grid)
    obstacle_key = "obstacle_for_navigation" if "obstacle_for_navigation" in grid.files else "occupied_raw"
    obstacles = np.asarray(grid[obstacle_key], dtype=bool)
    indoor = np.asarray(grid["indoor"], dtype=bool)
    reachable = np.asarray(grid["reachable"], dtype=bool)
    low = np.asarray(grid["low"], dtype=float)
    cell = float(np.asarray(grid["cell_reconstruction_units"]).item())

    final_mapped = apply(raw, args.transform)
    final_metrics = summarize(final_mapped, obstacles, indoor, reachable, low, cell, model_per_m)
    report: dict[str, object] = {
        "format": "uwb_obstacle_alignment_evaluation_v1",
        "trajectory": str(args.trail),
        "transform": str(args.transform),
        "obstacle_grid": str(args.grid),
        "obstacle_key": obstacle_key,
        "final": final_metrics,
    }
    baseline_mapped = None
    if args.baseline_transform is not None:
        baseline_mapped = apply(raw, args.baseline_transform)
        report["baseline_transform"] = str(args.baseline_transform)
        report["baseline"] = summarize(baseline_mapped, obstacles, indoor, reachable, low, cell, model_per_m)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.preview is not None:
        height, width = obstacles.shape
        high = low + np.asarray([width * cell, height * cell])
        extent = [low[0] / model_per_m, high[0] / model_per_m, low[1] / model_per_m, high[1] / model_per_m]
        fig, ax = plt.subplots(figsize=(12, 9))
        ax.imshow(obstacles.astype(np.uint8), origin="lower", extent=extent, cmap=ListedColormap(["#ffffff", "#282828"]), interpolation="nearest")
        if baseline_mapped is not None:
            ax.plot(baseline_mapped[:, 0], baseline_mapped[:, 1], color="#b5b5b5", linewidth=0.75, alpha=0.8, label="automatic initial")
        ax.plot(final_mapped[:, 0], final_mapped[:, 1], color="#0879c9", linewidth=0.9, alpha=0.92, label="manual final")
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(f"Original obstacle grid | final median boundary distance {final_metrics['median_boundary_distance_m']:.3f} m")
        ax.grid(True, color="#b8b8b8", linewidth=0.55, alpha=0.7)
        ax.legend(loc="best")
        fig.tight_layout()
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.preview, dpi=180)
        plt.close(fig)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
