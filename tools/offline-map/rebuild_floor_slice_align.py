#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Align a UWB boundary walk to a third-party reconstructed point cloud.

The reconstruction coordinate frame is not assumed to be upright.  The tool
first extracts several large plane candidates, selects a floor candidate using
camera-pose and geometric evidence, then slices the cloud around a chosen
height above the floor.  The UWB boundary trajectory is robustly registered to
that 2D slice.

All output is derived data: the original reconstruction files are never
modified.  The command prints stage progress so long point-cloud operations are
visible in a terminal.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import minimize
from scipy.spatial import ConvexHull, cKDTree


RNG_SEED = 20260720
TRANSFORM_FORMAT = "uwb_to_reconstruction_slice_transform_v1"


@dataclass
class PlaneCandidate:
    index: int
    normal: np.ndarray
    d: float
    center: np.ndarray
    point_count: int
    area: float
    score: float = 0.0
    camera_alignment: float = 0.0
    camera_height_median: float = float("nan")


def stage(current: int, total: int, message: str) -> None:
    print(f"[stage {current}/{total}] {message}", flush=True)


def read_binary_xyzrgb(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Read the simple binary little-endian PLY format used by this dataset."""
    with path.open("rb") as f:
        header = bytearray()
        while not header.endswith(b"end_header\n"):
            byte = f.read(1)
            if not byte:
                raise ValueError(f"invalid PLY header: {path}")
            header.extend(byte)
        offset = f.tell()

    header_text = header.decode("ascii", errors="replace")
    if "format binary_little_endian 1.0" not in header_text:
        raise ValueError("only binary_little_endian PLY is supported")
    try:
        count = int(next(line.split()[-1] for line in header_text.splitlines() if line.startswith("element vertex")))
    except StopIteration as exc:
        raise ValueError("PLY contains no vertex element") from exc

    # The supplied Pointcloud.ply contains x/y/z float32 followed by RGB u8.
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    expected = offset + count * dtype.itemsize
    if path.stat().st_size < expected:
        raise ValueError("PLY is shorter than its declared vertex count")

    raw = np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(count,))
    xyz = np.column_stack((raw["x"], raw["y"], raw["z"])).astype(np.float64)
    rgb = np.column_stack((raw["r"], raw["g"], raw["b"])).astype(np.uint8)
    valid = np.isfinite(xyz).all(axis=1)
    return xyz[valid], rgb[valid]


def read_camera_centers(path: Optional[Path]) -> Optional[np.ndarray]:
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("extrinsics", [])
    if not items:
        return None
    centers: List[np.ndarray] = []
    for item in items:
        matrix = np.asarray(item.get("matrix"), dtype=float)
        if matrix.shape != (4, 4):
            continue
        # camera.json stores an extrinsic matrix.  Its inverse translation is
        # the camera centre when the matrix follows the common world-to-camera convention.
        centers.append(np.linalg.inv(matrix)[:3, 3])
    if len(centers) < 3:
        return None
    return np.asarray(centers, dtype=float)


def robust_bounds(points: np.ndarray, low: float = 1.0, high: float = 99.0) -> Tuple[np.ndarray, np.ndarray]:
    return np.percentile(points, [low, high], axis=0)


def cloud_diagonal(points: np.ndarray) -> float:
    low, high = robust_bounds(points)
    return float(np.linalg.norm(high - low))


def planar_area(points: np.ndarray, normal: np.ndarray) -> float:
    centered = points - np.mean(points, axis=0)
    planar = centered - np.outer(centered @ normal, normal)
    _, _, vt = np.linalg.svd(planar, full_matrices=False)
    basis = vt[:2]
    projected = planar @ basis.T
    if len(projected) < 3:
        return 0.0
    try:
        return float(ConvexHull(projected).volume)
    except Exception:
        lo, hi = robust_bounds(projected)
        return float(np.prod(hi - lo))


def camera_motion_normal(centers: Optional[np.ndarray]) -> Optional[np.ndarray]:
    if centers is None or len(centers) < 4:
        return None
    centered = centers - np.mean(centers, axis=0)
    _, singular, vt = np.linalg.svd(centered, full_matrices=False)
    if singular[1] < 1e-5:
        return None
    normal = vt[-1]
    return normal / np.linalg.norm(normal)


def detect_plane_candidates(
    points: np.ndarray,
    diagonal: float,
    sample_count: int,
    plane_count: int,
    plane_threshold: Optional[float],
) -> List[PlaneCandidate]:
    # Open3D is only needed for plane detection. Keeping this import local
    # lets readers/exporters run after a floor transform has been confirmed.
    import open3d as o3d

    o3d.utility.random.seed(RNG_SEED)
    rng = np.random.default_rng(RNG_SEED)
    sample_count = min(sample_count, len(points))
    sample = points[rng.choice(len(points), size=sample_count, replace=False)]
    voxel = max(diagonal / 500.0, 0.005)
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(sample))
    reduced = pcd.voxel_down_sample(voxel)
    threshold = plane_threshold if plane_threshold is not None else max(diagonal * 0.003, voxel * 1.2)
    print(f"  plane sample={len(sample):,}, voxel={voxel:.4f}, reduced={len(reduced.points):,}, threshold={threshold:.4f}")

    candidates: List[PlaneCandidate] = []
    remaining = reduced
    for index in range(plane_count):
        if len(remaining.points) < 600:
            break
        model, inliers = remaining.segment_plane(distance_threshold=threshold, ransac_n=3, num_iterations=1500)
        hit = np.asarray(remaining.points)[np.asarray(inliers)]
        if len(hit) < 300:
            break
        normal = np.asarray(model[:3], dtype=float)
        normal /= np.linalg.norm(normal)
        candidates.append(
            PlaneCandidate(
                index=index,
                normal=normal,
                d=float(model[3]),
                center=np.median(hit, axis=0),
                point_count=len(hit),
                area=planar_area(hit, normal),
            )
        )
        remaining = remaining.select_by_index(inliers, invert=True)
    return candidates


def choose_floor(candidates: List[PlaneCandidate], cameras: Optional[np.ndarray]) -> PlaneCandidate:
    if not candidates:
        raise RuntimeError("no sufficiently large planar candidates found")
    motion_normal = camera_motion_normal(cameras)
    max_area = max(candidate.area for candidate in candidates) or 1.0
    max_count = max(candidate.point_count for candidate in candidates) or 1

    for candidate in candidates:
        if motion_normal is not None:
            candidate.camera_alignment = abs(float(candidate.normal @ motion_normal))
        else:
            candidate.camera_alignment = 0.5
        if cameras is not None:
            signed = (cameras - candidate.center) @ candidate.normal
            candidate.camera_height_median = float(np.median(np.abs(signed)))
        # A floor should be broad and be perpendicular to the approximate
        # constant-height camera motion plane.  Count remains a weak tiebreaker.
        candidate.score = (
            0.55 * math.sqrt(max(candidate.area, 0.0) / max_area)
            + 0.35 * candidate.camera_alignment
            + 0.10 * candidate.point_count / max_count
        )

    return max(candidates, key=lambda item: item.score)


def orient_floor_normal(floor: PlaneCandidate, cameras: Optional[np.ndarray]) -> np.ndarray:
    normal = floor.normal.copy()
    if cameras is not None and float(np.median((cameras - floor.center) @ normal)) < 0:
        normal *= -1.0
    return normal


def make_floor_basis(floor: PlaneCandidate, normal: np.ndarray, points: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    # Principal direction of nearby floor points gives a stable, reproducible X axis.
    dist = np.abs((points - floor.center) @ normal)
    near = points[dist <= max(np.percentile(dist, 8), 0.02)]
    centered = near - np.mean(near, axis=0)
    planar = centered - np.outer(centered @ normal, normal)
    _, _, vt = np.linalg.svd(planar, full_matrices=False)
    axis_x = vt[0] - float(vt[0] @ normal) * normal
    axis_x /= np.linalg.norm(axis_x)
    axis_y = np.cross(normal, axis_x)
    axis_y /= np.linalg.norm(axis_y)
    return axis_x, axis_y


def project_to_floor(points: np.ndarray, origin: np.ndarray, axis_x: np.ndarray, axis_y: np.ndarray) -> np.ndarray:
    rel = points - origin
    return np.column_stack((rel @ axis_x, rel @ axis_y))


def voxel_unique(points: np.ndarray, colors: np.ndarray, voxel: float) -> Tuple[np.ndarray, np.ndarray]:
    if len(points) == 0:
        return points, colors
    key = np.floor(points / voxel).astype(np.int64)
    _, first = np.unique(key, axis=0, return_index=True)
    return points[first], colors[first]


def load_uwb_xy(path: Path) -> np.ndarray:
    values: List[Tuple[float, float]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("valid_fix", "1")).strip().lower() in {"0", "false"}:
                continue
            if str(row.get("pos_updated", "1")).strip().lower() in {"0", "false"}:
                continue
            try:
                x, y = float(row["x_m"]), float(row["y_m"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(x) and math.isfinite(y):
                values.append((x, y))
    if len(values) < 20:
        raise ValueError("UWB trajectory has fewer than 20 valid points")
    return np.asarray(values, dtype=float)


def median_smooth(points: np.ndarray, window: int = 7) -> np.ndarray:
    if window <= 1:
        return points.copy()
    half = window // 2
    out = np.empty_like(points)
    for i in range(len(points)):
        out[i] = np.median(points[max(0, i - half) : min(len(points), i + half + 1)], axis=0)
    return out


def subsample_path(points: np.ndarray, min_gap: float = 0.035) -> np.ndarray:
    kept = [points[0]]
    for point in points[1:]:
        if np.linalg.norm(point - kept[-1]) >= min_gap:
            kept.append(point)
    return np.asarray(kept, dtype=float)


def apply_2d(points: np.ndarray, theta: float, scale: float, translation: np.ndarray) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    rotation = np.asarray([[c, -s], [s, c]])
    return scale * (points @ rotation.T) + translation


def trim_mean_distances(source: np.ndarray, tree: cKDTree, keep_fraction: float = 0.70) -> Tuple[float, np.ndarray]:
    distances, _ = tree.query(source, k=1, workers=-1)
    cutoff = max(1, int(len(distances) * keep_fraction))
    return float(np.mean(np.partition(distances, cutoff - 1)[:cutoff])), distances


def auto_align_uwb(
    uwb: np.ndarray,
    target: np.ndarray,
    allow_scale: bool,
    scale_prior: float,
) -> Tuple[float, float, np.ndarray, float, np.ndarray]:
    """Coarse rotation search followed by robust local optimisation."""
    if len(target) < 100:
        raise RuntimeError("the 1 m slice has too few points for alignment")
    tree = cKDTree(target)
    source_center = np.median(uwb, axis=0)
    target_center = np.median(target, axis=0)
    centered = uwb - source_center
    target_span = float(np.linalg.norm(np.ptp(target, axis=0)))

    if scale_prior <= 0:
        raise ValueError("scale prior must be positive")
    scale_values = [scale_prior] if not allow_scale else [scale_prior * factor for factor in (0.75, 0.88, 1.0, 1.12, 1.25)]

    best: Optional[Tuple[float, float, float, np.ndarray]] = None
    for theta in np.deg2rad(np.arange(0.0, 360.0, 4.0)):
        for scale in scale_values:
            mapped = apply_2d(centered, float(theta), float(scale), target_center)
            error, _ = trim_mean_distances(mapped, tree)
            if best is None or error < best[0]:
                best = (error, float(theta), float(scale), target_center.copy())
    assert best is not None

    def objective(params: np.ndarray) -> float:
        theta, dx, dy, raw_scale = params
        scale = math.exp(raw_scale) if allow_scale else scale_prior
        mapped = apply_2d(centered, theta, scale, target_center + np.asarray([dx, dy]))
        error, _ = trim_mean_distances(mapped, tree)
        return error

    start = np.asarray([best[1], 0.0, 0.0, math.log(best[2])], dtype=float)
    if not allow_scale:
        start[3] = math.log(scale_prior)
    bounds = [
        (-2.0 * math.pi, 2.0 * math.pi),
        (-target_span, target_span),
        (-target_span, target_span),
        (math.log(scale_prior * 0.65), math.log(scale_prior * 1.45)),
    ]
    result = minimize(
        objective,
        start,
        method="Powell",
        bounds=bounds,
        options={"maxiter": 180, "xtol": 1e-4, "ftol": 1e-4},
    )
    theta, dx, dy, raw_scale = result.x
    scale = math.exp(raw_scale) if allow_scale else scale_prior
    translation = target_center + np.asarray([dx, dy]) - scale * (
        np.asarray([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]]) @ source_center
    )
    mapped = apply_2d(uwb, theta, scale, translation)
    error, distances = trim_mean_distances(mapped, tree)
    return float(theta), float(scale), translation, error, distances


def save_preview(
    output: Path,
    slice_xy: np.ndarray,
    slice_rgb: np.ndarray,
    uwb_mapped: np.ndarray,
    floor: PlaneCandidate,
    height: float,
    thickness: float,
) -> None:
    fig, ax = plt.subplots(figsize=(9, 9), constrained_layout=True)
    if len(slice_rgb):
        colors = slice_rgb.astype(float) / 255.0
        ax.scatter(slice_xy[:, 0], slice_xy[:, 1], s=0.9, c=colors, alpha=0.42, linewidths=0, label="reconstruction slice")
    else:
        ax.scatter(slice_xy[:, 0], slice_xy[:, 1], s=0.9, color="#7f7f7f", alpha=0.42, linewidths=0, label="reconstruction slice")
    ax.plot(uwb_mapped[:, 0], uwb_mapped[:, 1], color="#e53935", linewidth=1.3, label="UWB outline")
    ax.plot(uwb_mapped[:1, 0], uwb_mapped[:1, 1], "go", markersize=6, label="UWB start")
    ax.plot(uwb_mapped[-1:, 0], uwb_mapped[-1:, 1], "ro", markersize=6, label="UWB end")
    ax.set_title(f"Floor candidate {floor.index}; slice {height:.2f} +/- {thickness:.2f}")
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("reconstruction floor X")
    ax.set_ylabel("reconstruction floor Y")
    ax.grid(True, linestyle="--", alpha=0.25)
    ax.legend(loc="best")
    fig.savefig(output, dpi=180)
    plt.close(fig)


def save_candidates_preview(
    output: Path,
    points: np.ndarray,
    candidates: Sequence[PlaneCandidate],
    diagonal: float,
) -> None:
    rng = np.random.default_rng(RNG_SEED + 1)
    subset = points[rng.choice(len(points), size=min(80000, len(points)), replace=False)]
    fig = plt.figure(figsize=(13, 8), constrained_layout=True)
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter(subset[:, 0], subset[:, 1], subset[:, 2], s=0.15, c="#929292", alpha=0.16)
    palette = ["#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa", "#00897b", "#6d4c41", "#546e7a"]
    for candidate in candidates:
        color = palette[candidate.index % len(palette)]
        length = diagonal * 0.12
        endpoint = candidate.center + candidate.normal * length
        ax.plot(*np.vstack((candidate.center, endpoint)).T, color=color, linewidth=2.2)
        ax.text(*endpoint, f"P{candidate.index}\n{candidate.score:.2f}", color=color)
    ax.set_title("Plane candidates: arrow = normal, label = floor score")
    ax.set_box_aspect(np.ptp(subset, axis=0))
    fig.savefig(output, dpi=180)
    plt.close(fig)


def candidate_payload(candidate: PlaneCandidate) -> dict[str, Any]:
    return {
        "index": candidate.index,
        "normal": candidate.normal.tolist(),
        "plane_d": candidate.d,
        "center": candidate.center.tolist(),
        "sample_inlier_count": candidate.point_count,
        "projected_area": candidate.area,
        "camera_motion_normal_alignment": candidate.camera_alignment,
        "camera_plane_distance_median": candidate.camera_height_median,
        "floor_score": candidate.score,
    }


def candidate_from_payload(payload: dict[str, Any]) -> PlaneCandidate:
    return PlaneCandidate(
        index=int(payload["index"]),
        normal=np.asarray(payload["normal"], dtype=float),
        d=float(payload["plane_d"]),
        center=np.asarray(payload["center"], dtype=float),
        point_count=int(payload.get("sample_inlier_count", 0)),
        area=float(payload.get("projected_area", 0.0)),
        score=float(payload.get("floor_score", 0.0)),
        camera_alignment=float(payload.get("camera_motion_normal_alignment", 0.0)),
        camera_height_median=float(payload.get("camera_plane_distance_median", float("nan"))),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Find a reconstructed floor, make a 1 m slice, and align a UWB outline")
    parser.add_argument("--pointcloud", required=True, type=Path)
    parser.add_argument("--camera-json", type=Path, default=None)
    parser.add_argument("--uwb-trail", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tag-height-m", type=float, default=1.0, help="physical tag/anchor height above the floor")
    parser.add_argument("--slice-half-thickness-m", type=float, default=0.18, help="physical half-thickness around tag height")
    parser.add_argument(
        "--slice-height-model",
        type=float,
        default=None,
        help="override slice height in reconstruction units; normally inferred from UWB-to-model scale",
    )
    parser.add_argument("--floor-index", default="auto", help="auto or a candidate index shown in the report")
    parser.add_argument(
        "--floor-report",
        type=Path,
        default=None,
        help="optional prior floor_slice_report.json; preserves its P numbering for a manual floor selection",
    )
    parser.add_argument("--plane-count", type=int, default=8)
    parser.add_argument("--plane-sample", type=int, default=250000)
    parser.add_argument("--plane-threshold", type=float, default=None)
    parser.add_argument("--slice-voxel", type=float, default=0.025)
    parser.add_argument("--lock-scale", action="store_true", help="do not estimate a UWB-to-reconstruction scale")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stage(1, 6, "reading binary point cloud")
    points, colors = read_binary_xyzrgb(args.pointcloud)
    diagonal = cloud_diagonal(points)
    print(f"  valid points={len(points):,}, robust diagonal={diagonal:.4f}")

    stage(2, 6, "loading camera poses and detecting planar candidates")
    cameras = read_camera_centers(args.camera_json)
    print(f"  camera centres={'none' if cameras is None else len(cameras)}")
    candidates = detect_plane_candidates(points, diagonal, args.plane_sample, args.plane_count, args.plane_threshold)
    floor_auto = choose_floor(candidates, cameras)
    for candidate in candidates:
        print(
            f"  P{candidate.index}: score={candidate.score:.3f}, count={candidate.point_count:,}, "
            f"area={candidate.area:.3f}, normal={np.round(candidate.normal, 4).tolist()}, "
            f"camera_align={candidate.camera_alignment:.3f}"
        )
    save_candidates_preview(args.output_dir / "plane_candidates.png", points, candidates, diagonal)

    selected_from_report = False
    if args.floor_report is not None:
        saved_report = json.loads(args.floor_report.read_text(encoding="utf-8"))
        saved_candidates = {int(item["index"]): candidate_from_payload(item) for item in saved_report["plane_candidates"]}
        requested = int(args.floor_index) if args.floor_index != "auto" else int(saved_report["selected_floor_candidate"])
        if requested not in saved_candidates:
            raise ValueError(f"floor candidate P{requested} does not exist in {args.floor_report}")
        floor = saved_candidates[requested]
        selected_from_report = True
        print(f"  using manually selected plane P{requested} from {args.floor_report}")
    elif args.floor_index == "auto":
        floor = floor_auto
    else:
        requested = int(args.floor_index)
        try:
            floor = next(candidate for candidate in candidates if candidate.index == requested)
        except StopIteration as exc:
            raise ValueError(f"floor candidate P{requested} does not exist") from exc
    normal = orient_floor_normal(floor, cameras)
    axis_x, axis_y = make_floor_basis(floor, normal, points)
    print(f"  selected floor=P{floor.index}, upward normal={np.round(normal, 6).tolist()}")

    stage(3, 6, "estimating reconstruction scale and extracting the tag-height slice")
    uwb = subsample_path(median_smooth(load_uwb_xy(args.uwb_trail)))
    full_xy = project_to_floor(points, floor.center, axis_x, axis_y)
    uwb_span = float(np.linalg.norm(np.ptp(uwb, axis=0)))
    model_span = float(np.linalg.norm(np.diff(robust_bounds(full_xy), axis=0)[0]))
    scale_prior = model_span / max(uwb_span, 1e-6)
    slice_height = args.slice_height_model if args.slice_height_model is not None else args.tag_height_m * scale_prior
    slice_thickness = args.slice_half_thickness_m * scale_prior
    print(
        f"  UWB/model span={uwb_span:.4f}/{model_span:.4f}; scale prior={scale_prior:.6f}; "
        f"tag-height slice={slice_height:.4f} +/- {slice_thickness:.4f} reconstruction units"
    )
    heights = (points - floor.center) @ normal
    mask = np.abs(heights - slice_height) <= slice_thickness
    slice_points = points[mask]
    slice_colors = colors[mask]
    slice_xy = project_to_floor(slice_points, floor.center, axis_x, axis_y)
    slice_xy, slice_colors = voxel_unique(slice_xy, slice_colors, args.slice_voxel)
    print(f"  raw slice points={int(mask.sum()):,}, voxel slice points={len(slice_xy):,}")
    if len(slice_xy) < 150:
        raise RuntimeError("slice is too sparse; increase --slice-thickness or choose another floor candidate")
    np.savez_compressed(args.output_dir / "slice_tag_height.npz", xy=slice_xy, rgb=slice_colors, height=slice_height, thickness=slice_thickness)

    stage(4, 6, "using the smoothed UWB outline")
    print(f"  UWB points after smoothing/subsampling={len(uwb):,}")

    stage(5, 6, "robust automatic UWB-to-slice registration")
    theta, scale, translation, trimmed_error, distances = auto_align_uwb(
        uwb, slice_xy, allow_scale=not args.lock_scale, scale_prior=scale_prior
    )
    uwb_mapped = apply_2d(uwb, theta, scale, translation)
    near_ratio = float(np.mean(distances <= max(args.slice_voxel * 3.0, 0.08 * max(scale, 1e-6))))
    print(
        f"  rotation={math.degrees(theta):.3f} deg, scale={scale:.6f}, translation={np.round(translation, 5).tolist()}, "
        f"trimmed nearest-slice error={trimmed_error:.4f}, near ratio={near_ratio:.3f}"
    )

    stage(6, 6, "writing alignment transform and diagnostic previews")
    rotation = np.asarray([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    transform = {
        "format": TRANSFORM_FORMAT,
        "source": "rebuild_floor_slice_align",
        "source_uwb_trail": str(args.uwb_trail),
        "source_pointcloud": str(args.pointcloud),
        "selected_floor_candidate": floor.index,
        "selected_floor_report": str(args.floor_report) if selected_from_report else None,
        "floor_origin_3d": floor.center.tolist(),
        "floor_up_normal_3d": normal.tolist(),
        "floor_axis_x_3d": axis_x.tolist(),
        "floor_axis_y_3d": axis_y.tolist(),
        "tag_height_m": args.tag_height_m,
        "slice_half_thickness_m": args.slice_half_thickness_m,
        "slice_height_reconstruction_units": slice_height,
        "slice_half_thickness_reconstruction_units": slice_thickness,
        "scale_prior": scale_prior,
        "rotation_deg": math.degrees(theta),
        "rotation_matrix": rotation.tolist(),
        "scale": scale,
        "translation_slice_xy": translation.tolist(),
        "scale_locked": bool(args.lock_scale),
        "quality": {
            "trimmed_nearest_slice_error": trimmed_error,
            "near_slice_ratio": near_ratio,
            "uwb_point_count": len(uwb),
            "slice_point_count": len(slice_xy),
        },
    }
    report = {
        "format": "rebuild_floor_slice_report_v1",
        "pointcloud": str(args.pointcloud),
        "camera_json": str(args.camera_json) if args.camera_json else None,
        "point_count": int(len(points)),
        "robust_diagonal": diagonal,
        "plane_candidates": [candidate_payload(candidate) for candidate in candidates],
        "automatic_floor_candidate": floor_auto.index,
        "selected_floor_candidate": floor.index,
        "selected_floor_report": str(args.floor_report) if selected_from_report else None,
        "transform_file": "uwb_to_rebuild_slice_transform.json",
        "preview_file": "uwb_on_rebuild_slice.png",
        "slice_file": "slice_tag_height.npz",
    }
    (args.output_dir / "uwb_to_rebuild_slice_transform.json").write_text(json.dumps(transform, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "floor_slice_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save_preview(args.output_dir / "uwb_on_rebuild_slice.png", slice_xy, slice_colors, uwb_mapped, floor, slice_height, slice_thickness)
    print(f"done: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
