#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract trolley-reachable 2D free space from a reconstructed point cloud.

The floor itself is excluded.  All reconstructed structure between a small
floor clearance and a chosen height is treated as an obstacle.  The script
closes small wall gaps, fills the indoor envelope, inflates obstacles for a
trolley, and keeps the largest free-space component (or the one selected by a
seed point).  Raw reconstruction data is never changed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage

from rebuild_floor_slice_align import read_binary_xyzrgb


def disk(radius_cells: int) -> np.ndarray:
    if radius_cells <= 0:
        return np.ones((1, 1), dtype=bool)
    coords = np.arange(-radius_cells, radius_cells + 1)
    xx, yy = np.meshgrid(coords, coords)
    return (xx * xx + yy * yy) <= radius_cells * radius_cells


def floor_xy(points: np.ndarray, origin: np.ndarray, axis_x: np.ndarray, axis_y: np.ndarray) -> np.ndarray:
    rel = points - origin
    return np.column_stack((rel @ axis_x, rel @ axis_y))


def grid_index(xy: np.ndarray, low: np.ndarray, cell: float, shape: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    ix = np.floor((xy[:, 0] - low[0]) / cell).astype(int)
    iy = np.floor((xy[:, 1] - low[1]) / cell).astype(int)
    valid = (ix >= 0) & (ix < shape[1]) & (iy >= 0) & (iy < shape[0])
    return ix, iy, valid


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract 0-to-1 m trolley-reachable space from a reconstruction")
    parser.add_argument("--pointcloud", required=True, type=Path)
    parser.add_argument("--transform", required=True, type=Path, help="confirmed-floor transform JSON")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--height-m", type=float, default=1.0, help="obstacle height above floor")
    parser.add_argument("--floor-clearance-m", type=float, default=0.08, help="ignore ground returns below this height")
    parser.add_argument("--cell-m", type=float, default=0.035, help="navigation grid cell size in physical metres")
    parser.add_argument("--min-cell-points", type=int, default=5, help="minimum vertical-band points for an occupied cell")
    parser.add_argument("--wall-close-m", type=float, default=0.09, help="close small reconstruction gaps in walls")
    parser.add_argument(
        "--outer-only",
        action="store_true",
        help="ignore detached interior obstacles and retain only the outer room enclosure",
    )
    parser.add_argument(
        "--outer-wall-thickness-m",
        type=float,
        default=0.035,
        help="wall thickness applied to the recovered outer boundary when --outer-only is used",
    )
    parser.add_argument(
        "--keep-rect",
        type=float,
        nargs=4,
        action="append",
        metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
        default=[],
        help="retain original obstacles inside this reconstruction-floor rectangle; may be repeated",
    )
    parser.add_argument("--cart-width-m", type=float, default=0.0, help="trolley width; 0 extracts point-robot free space")
    parser.add_argument("--safety-margin-m", type=float, default=0.0, help="extra clearance on each trolley side")
    parser.add_argument("--seed-x", type=float, default=None, help="optional reachable seed in reconstruction floor X")
    parser.add_argument("--seed-y", type=float, default=None, help="optional reachable seed in reconstruction floor Y")
    args = parser.parse_args()
    if (args.seed_x is None) != (args.seed_y is None):
        raise ValueError("--seed-x and --seed-y must be supplied together")
    if args.height_m <= args.floor_clearance_m:
        raise ValueError("--height-m must exceed --floor-clearance-m")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    transform = json.loads(args.transform.read_text(encoding="utf-8"))
    origin = np.asarray(transform["floor_origin_3d"], dtype=float)
    normal = np.asarray(transform["floor_up_normal_3d"], dtype=float)
    axis_x = np.asarray(transform["floor_axis_x_3d"], dtype=float)
    axis_y = np.asarray(transform["floor_axis_y_3d"], dtype=float)
    model_per_m = float(transform["scale_prior"])
    cell = args.cell_m * model_per_m
    floor_clearance = args.floor_clearance_m * model_per_m
    max_height = args.height_m * model_per_m

    print("[stage 1/5] reading point cloud", flush=True)
    points, _ = read_binary_xyzrgb(args.pointcloud)
    rel = points - origin
    height = rel @ normal
    all_xy = np.column_stack((rel @ axis_x, rel @ axis_y))
    low, high = np.percentile(all_xy, [0.4, 99.6], axis=0)
    pad = max(0.18 * model_per_m, cell * 4.0)
    low -= pad
    high += pad
    width = int(math.ceil((high[0] - low[0]) / cell)) + 1
    grid_height = int(math.ceil((high[1] - low[1]) / cell)) + 1
    shape = (grid_height, width)
    print(f"  grid={width} x {grid_height}, cell={args.cell_m:.3f} m")

    print("[stage 2/5] projecting vertical 0-to-1 m obstacle band", flush=True)
    vertical = (height >= floor_clearance) & (height <= max_height)
    obstacle_xy = all_xy[vertical]
    ix, iy, valid = grid_index(obstacle_xy, low, cell, shape)
    counts = np.zeros(shape, dtype=np.uint32)
    np.add.at(counts, (iy[valid], ix[valid]), 1)
    occupied_raw = counts >= args.min_cell_points
    print(f"  obstacle-band points={len(obstacle_xy):,}, occupied cells={int(occupied_raw.sum()):,}")

    print("[stage 3/5] closing wall gaps and recovering indoor envelope", flush=True)
    close_radius = max(1, int(math.ceil(args.wall_close_m * model_per_m / cell)))
    wall_mask = ndimage.binary_dilation(occupied_raw, structure=disk(close_radius))
    wall_mask = ndimage.binary_closing(wall_mask, structure=disk(close_radius))
    indoor = ndimage.binary_fill_holes(wall_mask)
    # Keep only the largest enclosed indoor region. This rejects empty exterior
    # space if a partial reconstruction leaves it in the raster bounds.
    indoor_labels, indoor_count = ndimage.label(indoor)
    if indoor_count == 0:
        raise RuntimeError("no enclosed indoor footprint found; increase --wall-close-m")
    sizes = np.bincount(indoor_labels.ravel())
    sizes[0] = 0
    indoor = indoor_labels == int(np.argmax(sizes))

    print("[stage 4/5] inflating obstacles for trolley clearance", flush=True)
    clearance_m = args.cart_width_m / 2.0 + args.safety_margin_m
    clearance_cells = int(math.ceil(clearance_m * model_per_m / cell))
    if args.outer_only:
        # ``indoor`` has no internal holes after binary_fill_holes, therefore
        # this is the perimeter of the fixed room envelope only. Detached
        # furniture/chair point clusters are intentionally absent here.
        outer_edge = indoor & ~ndimage.binary_erosion(indoor, structure=disk(1))
        outer_wall_cells = max(1, int(math.ceil(args.outer_wall_thickness_m * model_per_m / cell)))
        obstacle_for_navigation = ndimage.binary_dilation(outer_edge, structure=disk(outer_wall_cells))
        obstacle_label = "outer fixed enclosure only"
    else:
        obstacle_for_navigation = occupied_raw
        obstacle_label = "all reconstructed vertical obstacles"
    if args.keep_rect:
        # A retained rectangle is for fixed structures that are geometrically
        # inside the outer envelope (e.g. a partition), unlike movable chairs.
        xx = low[0] + (np.arange(shape[1]) + 0.5) * cell
        yy = low[1] + (np.arange(shape[0]) + 0.5) * cell
        grid_x, grid_y = np.meshgrid(xx, yy)
        retained = np.zeros(shape, dtype=bool)
        for xmin, ymin, xmax, ymax in args.keep_rect:
            lo_x, hi_x = sorted((xmin, xmax))
            lo_y, hi_y = sorted((ymin, ymax))
            retained |= (grid_x >= lo_x) & (grid_x <= hi_x) & (grid_y >= lo_y) & (grid_y <= hi_y)
        obstacle_for_navigation |= occupied_raw & retained
        obstacle_label += " + manually retained fixed structures"
    inflated = (
        ndimage.binary_dilation(obstacle_for_navigation, structure=disk(clearance_cells))
        if clearance_cells
        else obstacle_for_navigation.copy()
    )
    free = indoor & ~inflated
    labels, count = ndimage.label(free)
    if count == 0:
        raise RuntimeError("no free space remains; reduce cart width/margin or inspect obstacle settings")
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    if args.seed_x is None:
        chosen = int(np.argmax(sizes))
        seed_info = "largest component"
    else:
        seed_ix, seed_iy, seed_valid = grid_index(np.asarray([[args.seed_x, args.seed_y]]), low, cell, shape)
        if not seed_valid[0] or labels[seed_iy[0], seed_ix[0]] == 0:
            raise RuntimeError("seed does not fall in reachable free space")
        chosen = int(labels[seed_iy[0], seed_ix[0]])
        seed_info = f"seed ({args.seed_x:.3f}, {args.seed_y:.3f})"
    reachable = labels == chosen
    print(f"  clearance={clearance_m:.3f} m, reachable cells={int(reachable.sum()):,}, selection={seed_info}")

    print("[stage 5/5] saving navigability contour and preview", flush=True)
    x = low[0] + (np.arange(width) + 0.5) * cell
    y = low[1] + (np.arange(grid_height) + 0.5) * cell
    fig, ax = plt.subplots(figsize=(11, 9), constrained_layout=True)
    ax.imshow(
        obstacle_for_navigation,
        origin="lower",
        extent=[low[0], high[0], low[1], high[1]],
        cmap="Greys",
        alpha=0.82,
        interpolation="nearest",
    )
    ax.contour(x, y, indoor.astype(float), levels=[0.5], colors="#1565c0", linewidths=1.2, linestyles="--")
    contours = ax.contour(x, y, reachable.astype(float), levels=[0.5], colors="#00a152", linewidths=2.0)
    ax.set_title(
        f"Trolley reachable space: {obstacle_label}; clearance {clearance_m:.2f} m"
    )
    ax.set_xlabel("reconstruction floor X")
    ax.set_ylabel("reconstruction floor Y")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, color="#cfcfcf", linewidth=0.6)
    ax.text(0.01, 0.01, "black: navigation obstacles | blue: indoor envelope | green: reachable trolley-centre boundary", transform=ax.transAxes, fontsize=9, va="bottom")
    fig.savefig(args.output_dir / "reachable_space_preview.png", dpi=220, facecolor="white")

    segments = contours.allsegs[0]
    if segments:
        longest = max(segments, key=len)
        with (args.output_dir / "reachable_boundary.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["floor_x_reconstruction", "floor_y_reconstruction"])
            writer.writerows(longest.tolist())
    plt.close(fig)
    np.savez_compressed(
        args.output_dir / "navigation_grid.npz",
        occupied_raw=occupied_raw,
        obstacle_for_navigation=obstacle_for_navigation,
        indoor=indoor,
        reachable=reachable,
        low=low,
        cell_reconstruction_units=cell,
    )
    metadata = {
        "format": "reconstruction_accessible_space_v1",
        "source_pointcloud": str(args.pointcloud),
        "source_transform": str(args.transform),
        "floor_clearance_m": args.floor_clearance_m,
        "height_m": args.height_m,
        "grid_cell_m": args.cell_m,
        "cart_width_m": args.cart_width_m,
        "safety_margin_m": args.safety_margin_m,
        "effective_clearance_m": clearance_m,
        "outer_only": bool(args.outer_only),
        "outer_wall_thickness_m": args.outer_wall_thickness_m if args.outer_only else None,
        "keep_rectangles_reconstruction_floor": args.keep_rect,
        "reachable_component_selection": seed_info,
        "reachable_cell_count": int(reachable.sum()),
        "boundary_file": "reachable_boundary.csv",
        "preview_file": "reachable_space_preview.png",
    }
    (args.output_dir / "accessible_space_summary.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done: {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
