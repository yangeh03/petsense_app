#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a high-contrast, UWB-free horizontal reconstruction slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from rebuild_floor_slice_align import read_binary_xyzrgb


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a raw high-contrast 1 m reconstruction slice")
    parser.add_argument("--pointcloud", required=True, type=Path)
    parser.add_argument("--transform", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--point-size", type=float, default=0.55)
    parser.add_argument("--alpha", type=float, default=0.20)
    args = parser.parse_args()

    transform = json.loads(args.transform.read_text(encoding="utf-8"))
    origin = np.asarray(transform["floor_origin_3d"], dtype=float)
    normal = np.asarray(transform["floor_up_normal_3d"], dtype=float)
    axis_x = np.asarray(transform["floor_axis_x_3d"], dtype=float)
    axis_y = np.asarray(transform["floor_axis_y_3d"], dtype=float)
    height = float(transform["slice_height_reconstruction_units"])
    half_thickness = float(transform["slice_half_thickness_reconstruction_units"])

    print("[1/3] reading point cloud", flush=True)
    points, _ = read_binary_xyzrgb(args.pointcloud)
    print("[2/3] extracting raw 1 m height band", flush=True)
    rel = points - origin
    mask = np.abs(rel @ normal - height) <= half_thickness
    sliced = rel[mask]
    xy = np.column_stack((sliced @ axis_x, sliced @ axis_y))
    print(f"  raw slice points: {len(xy):,}", flush=True)

    print("[3/3] writing high-contrast slice image", flush=True)
    fig, ax = plt.subplots(figsize=(11, 9), constrained_layout=True)
    ax.scatter(xy[:, 0], xy[:, 1], s=args.point_size, c="#050505", alpha=args.alpha, linewidths=0, rasterized=True)
    ax.set_title(f"Reconstruction slice: physical 1.0 m above confirmed floor (band +/- {transform['slice_half_thickness_m']:.2f} m)")
    ax.set_xlabel("reconstruction floor X")
    ax.set_ylabel("reconstruction floor Y")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, color="#d0d0d0", linewidth=0.7)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220, facecolor="white")
    plt.close(fig)
    print(f"saved {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
