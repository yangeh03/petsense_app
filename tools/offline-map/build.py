#!/usr/bin/env python3
"""Build a versioned map bundle from a PLY and a confirmed floor transform."""
import argparse
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

from rebuild_floor_slice_align import read_binary_xyzrgb


def rasterize(scene, colors, low, extent, resolution, min_height, max_height):
    width, height = np.ceil(extent / resolution).astype(int)
    if width * height > 20_000_000:
        raise ValueError('Raster too large; increase resolution')
    kept = (scene[:, 2] >= min_height) & (scene[:, 2] <= max_height)
    inside = ((scene[:, :2] >= low) & (scene[:, :2] < low + extent)).all(axis=1)
    idx = np.flatnonzero(kept & inside)
    idx = idx[np.argsort(scene[idx, 2])[::-1]]
    pixel = np.floor((scene[idx, :2]-low)/resolution).astype(int)
    flat = pixel[:, 1]*width + pixel[:, 0]
    _, first = np.unique(flat, return_index=True)
    raster = np.full((height*width, 3), 248, dtype=np.uint8)
    raster[flat[first]] = colors[idx[first]]
    return raster.reshape(height, width, 3)[::-1]


def build(args):
    if args.output.exists():
        raise ValueError('Output already exists; use a new directory/version')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', s) for s in [args.map_id, args.version]):
        raise ValueError('Map ID and version must contain only letters, numbers, underscore or dash')
    if len(set(args.anchor_indices)) != 3 or any(not 0 <= i < 8 for i in args.anchor_indices):
        raise ValueError('Three distinct anchor indices in 0..7 are required')
    if not (-.5 <= args.min_height < args.max_height <= 4):
        raise ValueError('Invalid height interval')
    if not .005 <= args.resolution <= .1:
        raise ValueError('Resolution must be between 0.005 and 0.1 metres/pixel')
    floor = json.loads(args.floor.read_text())
    scale = float(floor['scale_prior'])
    basis = np.asarray([floor['floor_axis_x_3d'], floor['floor_axis_y_3d'], floor['floor_up_normal_3d']], dtype=float)
    origin = np.asarray(floor['floor_origin_3d'], dtype=float)
    if not np.isfinite(scale) or scale <= 0 or basis.shape != (3, 3) or origin.shape != (3,):
        raise ValueError('Invalid confirmed floor')
    if not np.isfinite(origin).all() or not np.allclose(basis @ basis.T, np.eye(3), atol=1e-5):
        raise ValueError('Floor axes must be finite and orthonormal')
    anchors = json.loads(args.anchors.read_text())['anchors_xyz_m']
    alignment = json.loads(args.alignment.read_text())
    points, colors = read_binary_xyzrgb(args.pointcloud)
    scene = (points - origin) @ basis.T / scale
    kept = (scene[:, 2] >= args.min_height) & (scene[:, 2] <= args.max_height)
    if kept.sum() < 100:
        raise ValueError('Height band is empty or too sparse')
    args.output.mkdir(parents=True)
    here = Path(__file__).parent
    subprocess.run([sys.executable, str(here/'rebuild_accessible_space.py'),
                    '--pointcloud', str(args.pointcloud.resolve()), '--transform', str(args.floor.resolve()),
                    '--height-m', str(args.obstacle_height), '--output-dir', str(args.output/'diagnostics')], check=True)
    subprocess.run([sys.executable, str(here/'rebuild_export_accessible_map.py'),
                    '--boundary', str(args.output/'diagnostics/reachable_boundary.csv'),
                    '--floor-transform', str(args.floor.resolve()), '--output', str(args.output/'polygon.json')], check=True)
    grid = np.load(args.output/'diagnostics/navigation_grid.npz')
    low = grid['low'] / scale
    h, w = grid['reachable'].shape
    extent = np.array([w, h]) * float(grid['cell_reconstruction_units']) / scale
    raster = rasterize(scene, colors, low, extent, args.resolution, args.min_height, args.max_height)
    height, width = raster.shape[:2]
    Image.fromarray(raster).save(args.output/'floorplan.png')
    polygon = json.loads((args.output/'polygon.json').read_text())['coordinates']
    manifest = {
        'schemaVersion': 1, 'mapId': args.map_id, 'version': args.version,
        'name': args.name, 'units': 'm', 'polygon': polygon,
        'anchors': [{'id': i, 'uwb': anchors[f'A{i}'][:2]} for i in args.anchor_indices],
        'uwbTransform': {'scale': alignment['scale'], 'matrix': alignment['rotation_matrix'],
                         'translation': alignment['translation_m']},
        'background': {'file': 'floorplan.png', 'origin': low.tolist(),
                       'metersPerPixel': args.resolution, 'width': int(width), 'height': int(height)},
        'staleAfterMs': 2000,
        'provenance': {'pointcloud': args.pointcloud.name, 'floor': args.floor.name,
                       'selectedFloor': floor.get('selected_floor_candidate'),
                       'modelUnitsPerMeter': scale, 'heightBandM': [args.min_height, args.max_height],
                       'note': 'Height crop, not semantic ceiling segmentation; scale/floor require confirmation.'},
    }
    (args.output/'map.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    shutil.copyfile(args.floor, args.output/'confirmed-floor.json')
    print(f'Map bundle written to {args.output}; assign device and Tag separately in runtime bindings.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ['pointcloud', 'floor', 'anchors', 'alignment', 'output']:
        parser.add_argument('--'+flag, type=Path, required=True)
    parser.add_argument('--map-id', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--name', default='重建空间')
    parser.add_argument('--min-height', type=float, default=-.05)
    parser.add_argument('--max-height', type=float, default=1.2)
    parser.add_argument('--obstacle-height', type=float, default=1.)
    parser.add_argument('--resolution', type=float, default=.01)
    parser.add_argument('--anchor-indices', type=int, nargs=3, default=[0, 1, 2])
    build(parser.parse_args())


if __name__ == '__main__':
    main()
