#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export a reconstruction-floor reachable boundary as a metre-scaled map JSON."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from uwb_map_geometry import rdp_simplify


def main() -> int:
    parser = argparse.ArgumentParser(description="Export reachable boundary CSV to uwb_map_model_v1 JSON")
    parser.add_argument("--boundary", required=True, type=Path)
    parser.add_argument("--floor-transform", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--simplify-m", type=float, default=0.04)
    args = parser.parse_args()

    transform = json.loads(args.floor_transform.read_text(encoding="utf-8"))
    model_per_m = float(transform["scale_prior"])
    if model_per_m <= 0:
        raise ValueError("floor transform contains invalid scale_prior")
    points = []
    with args.boundary.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            points.append((float(row["floor_x_reconstruction"]) / model_per_m, float(row["floor_y_reconstruction"]) / model_per_m))
    if len(points) < 3:
        raise ValueError("boundary needs at least three points")
    if np.linalg.norm(np.asarray(points[0]) - np.asarray(points[-1])) < 1e-8:
        points = points[:-1]
    simplified = rdp_simplify(points + [points[0]], args.simplify_m)
    if len(simplified) > 1 and np.linalg.norm(np.asarray(simplified[0]) - np.asarray(simplified[-1])) < 1e-8:
        simplified = simplified[:-1]
    payload = {
        "format": "uwb_map_model_v1",
        "kind": "polygon",
        "name": "reconstruction_accessible_outer_boundary_no_clearance",
        "coordinates": simplified,
        "source_boundary": str(args.boundary),
        "reconstruction_units_per_meter": model_per_m,
        "simplify_m": args.simplify_m,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported {len(points)} boundary samples -> {len(simplified)} polygon vertices")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
