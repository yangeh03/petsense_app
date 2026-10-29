#!/usr/bin/env python3
"""Run the ZIP's grid registration and polygon diagnostics on a point-cloud map.

The original algorithm is unchanged in uwb_polygon_boundary_align.py. This
adapter runs the final grid_global_x_scale default plus polygon diagnostics, and creates
versioned bundles for authenticated review and publication.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from scipy import ndimage, optimize

from validate import validate_bundle
import uwb_polygon_boundary_align as original
import rebuild_benchmark_auto_alignment as benchmark
import rebuild_evaluate_obstacle_alignment as evaluation


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


def quality(report):
    return (report['inlier_ratio'] >= .65 and report['boundary_coverage_ratio'] >= .65
            and report['median_edge_error_m'] <= .25 and report['outside_far_ratio'] <= .15)


def grid_inputs(native, root):
    floor = json.loads((root / 'confirmed-floor.json').read_text(encoding='utf-8'))
    grid = np.load(native / 'diagnostics/navigation_grid.npz')
    key = 'obstacle_for_navigation' if 'obstacle_for_navigation' in grid.files else 'occupied_raw'
    obstacles = np.asarray(grid[key], dtype=bool)
    boundary = ndimage.binary_dilation(obstacles, structure=np.ones((3, 3), dtype=bool)) ^ ndimage.binary_erosion(obstacles, structure=np.ones((3, 3), dtype=bool))
    cell, units = float(grid['cell_reconstruction_units']), float(floor['scale_prior'])
    low = np.asarray(grid['low'])
    distance = ndimage.distance_transform_edt(~boundary) * cell / units
    return grid, obstacles, low, cell, units, distance


def grid_fit(raw, native, root):
    grid, obstacles, low, cell, units, distance = grid_inputs(native, root)
    keep = np.linspace(0, len(raw) - 1, min(450, len(raw))).round().astype(int)
    objective = benchmark.GridDistanceObjective(raw[keep], distance, low, cell, units)
    high = low + np.asarray([obstacles.shape[1] * cell, obstacles.shape[0] * cell])
    bounds = [(-np.pi, np.pi), (low[0] / units - 4.5, high[0] / units + 4.5),
              (low[1] / units - 4.5, high[1] / units + 4.5), (.85, 1.15)]
    # Exact settings of the final default documented in ZIP HANDOFF.md.
    # No ground truth/manual transform participates in this objective.
    fitted = optimize.differential_evolution(
        lambda p: objective.score(p, benchmark.MIRRORS['x'], allow_scale=True),
        bounds=bounds, maxiter=45, popsize=8, seed=20260730,
        polish=True, updating='immediate', workers=1)
    return benchmark.transform_from_params(fitted.x, benchmark.MIRRORS['x'], True)


def floor_to_map(transform, manifest):
    matrix, translation, scale = transform
    orientation = manifest['provenance']['orientation']
    rotation = np.asarray(orientation['matrix'])
    offset = np.asarray(orientation['translationM'])
    return rotation @ matrix, rotation @ translation + offset, scale


def extra_candidate(name, transform, raw, native, root, manifest, job, manual=False):
    matrix, translation, scale = transform
    if (matrix.shape != (2, 2) or translation.shape != (2,) or not np.isfinite(matrix).all()
            or not np.isfinite(translation).all() or not np.isfinite(scale) or not .7 <= scale <= 1.3
            or not np.allclose(matrix @ matrix.T, np.eye(2), atol=1e-7)):
        raise ValueError('manual/grid transform must be finite, rigid or mirrored, with bounded scale')
    grid, obstacles, low, cell, units, _ = grid_inputs(native, root)
    floor_mapped = benchmark.apply(raw, matrix, translation, scale)
    metrics = evaluation.summarize(floor_mapped, obstacles, grid['indoor'].astype(bool),
                                  grid['reachable'].astype(bool), low, cell, units)
    final = floor_to_map(transform, manifest)
    mapped = benchmark.apply(raw, *final)
    polygon = np.asarray(manifest['polygon'])
    score = original.score_candidate(mapped, polygon, .32, .55, .20)
    report = {'method': 'manual-confirmed' if manual else 'grid_global_x_scale', 'scale': scale,
              'scale_locked': scale == 1, 'boundary_coverage_ratio': score['boundary_coverage_ratio'],
              'median_edge_error_m': metrics['median_boundary_distance_m'],
              'inlier_ratio': metrics['within_0p20m_ratio'], 'grid': metrics}
    indoor_pixels = np.argwhere(grid['indoor'])
    indoor_extent = (indoor_pixels.max(axis=0) - indoor_pixels.min(axis=0) + 1)[::-1] * cell / units
    span_ratio = np.ptp(floor_mapped, axis=0) / indoor_extent
    report['spanRatio'] = span_ratio.tolist()
    eligible = (metrics['in_grid_ratio'] >= .95 and metrics['inside_indoor_ratio'] >= .85
                and metrics['median_boundary_distance_m'] is not None and metrics['median_boundary_distance_m'] <= .15
                and metrics['p90_boundary_distance_m'] <= .4 and np.min(span_ratio) >= .45)
    if manual:
        # Explicitly reviewed imports may retain the ZIP's low polygon coverage.
        eligible = metrics['in_grid_ratio'] >= .9 and metrics['inside_indoor_ratio'] >= .8
    bundle = root / ('bundle-' + name)
    bundle.mkdir()
    shutil.copyfile(native / 'floorplan.png', bundle / 'floorplan.png')
    result = json.loads(json.dumps(manifest))
    result['version'] = job['version'] + '-' + name
    result['uwbTransform'] = {'scale': final[2], 'matrix': final[0].tolist(), 'translation': final[1].tolist()}
    result['provenance']['orientation']['sourceUwbTransform'] = {
        'scale': scale, 'matrix': matrix.tolist(), 'translation': translation.tolist()}
    result['provenance']['boundaryAlignment'] = {'walkId': job['walkId'], 'candidate': name, 'report': report}
    write(bundle / 'map.json', result)
    write(root / (name + '-report.json'), report)
    benchmark.write_transform(root / (name + '.json'), transform, report['method'])
    original.plot_preview(root / (name + '-preview.png'), raw, raw, polygon,
        {**score, 'mapped': mapped, 'theta_deg': float(np.degrees(np.arctan2(final[0][1, 0], final[0][0, 0])))})
    validate_bundle(bundle)
    return {'id': name, 'eligible': bool(eligible), 'requiresManualConfirmation': True,
            'report': report, 'trail': original.evenly_subsample(mapped, 600).tolist()}


def run(job_path):
    job = json.loads(job_path.read_text(encoding='utf-8'))
    root = job_path.parent
    here = Path(__file__).parent
    native = root / 'native'
    identity = root / 'identity.json'
    write(identity, {'scale': 1, 'rotation_matrix': [[1, 0], [0, 1]], 'translation_m': [0, 0]})
    subprocess.run([sys.executable, str(here / 'build.py'),
                    '--pointcloud', str(root / 'Pointcloud.ply'),
                    '--floor', str(root / 'confirmed-floor.json'),
                    '--anchors', str(root / 'anchors.json'), '--alignment', str(identity),
                    '--output', str(native), '--map-id', job['mapId'], '--version', job['version'],
                    '--name', '手动建图空间', '--resolution', '.02'], check=True)
    # polygon.json already has the same orientation/origin as floorplan.png.
    # Fit directly into this final frame; do not compose its orientation twice.
    candidates = []
    raw = original.load_xy_csv(root / 'trail.csv', 'x_m', 'y_m', updated_only=True)
    clean = original.preprocess(raw, 5, .75, .015)
    if len(clean) < 10:
        raise ValueError('too few clean boundary points')
    polygon = np.asarray(json.loads((native / 'map.json').read_text(encoding='utf-8'))['polygon'])
    manifest = json.loads((native / 'map.json').read_text(encoding='utf-8'))
    raw_grid = benchmark.load_raw_trail(root / 'trail.csv')
    print('Running ZIP grid_global_x_scale (raw trajectory, X mirror, bounded scale .85–1.15)', flush=True)
    candidates.append(extra_candidate('grid', grid_fit(raw_grid, native, root), raw_grid, native, root, manifest, job))
    if (root / 'alignment.json').is_file():
        candidates.append(extra_candidate('manual', benchmark.load_transform(root / 'alignment.json'),
                                          raw_grid, native, root, manifest, job, manual=True))
    for name, mirror in [('normal', False), ('mirror', True)]:
        prefix = root / name
        command = [sys.executable, str(here / 'uwb_polygon_boundary_align.py'),
                   '-i', str(root / 'trail.csv'), '-m', str(native / 'polygon.json'),
                   '-o', str(prefix.with_suffix('.json')), '--report', str(root / f'{name}-report.json'),
                   '--preview-png', str(root / f'{name}-preview.png'),
                   '--inlier-csv', str(root / f'{name}-inliers.csv'),
                   '--angle-step', '3', '--edge-tol', '.32', '--max-search-points', '350']
        if mirror:
            command.append('--mirror-uwb-x')
        subprocess.run(command, check=True)
        transform = json.loads(prefix.with_suffix('.json').read_text(encoding='utf-8'))
        report = json.loads((root / f'{name}-report.json').read_text(encoding='utf-8'))
        mapped = original.apply_transform(clean, transform['scale'],
                    np.asarray(transform['rotation_matrix']), np.asarray(transform['translation_m']))
        bundle = root / f'bundle-{name}'
        bundle.mkdir()
        shutil.copyfile(native / 'floorplan.png', bundle / 'floorplan.png')
        manifest = json.loads((native / 'map.json').read_text(encoding='utf-8'))
        manifest['version'] = job['version'] + '-' + name
        manifest['uwbTransform'] = {'scale': transform['scale'], 'matrix': transform['rotation_matrix'],
                                    'translation': transform['translation_m']}
        orientation = manifest['provenance']['orientation']
        floor_to_map = np.asarray(orientation['matrix'])
        offset = np.asarray(orientation['translationM'])
        orientation['sourceUwbTransform'] = {
            'scale': transform['scale'],
            'matrix': (floor_to_map.T @ np.asarray(transform['rotation_matrix'])).tolist(),
            'translation': (floor_to_map.T @ (np.asarray(transform['translation_m']) - offset)).tolist()}
        manifest['provenance']['boundaryAlignment'] = {'source': 'ZIP uwb_polygon_boundary_align',
            'walkId': job['walkId'], 'candidate': name, 'report': report,
            'note': 'Fit directly to final oriented polygon; no additional orientation transform.'}
        write(bundle / 'map.json', manifest)
        validate_bundle(bundle)
        # Re-score transformed points against the final polygon as a frame-consistency check.
        score = original.score_candidate(mapped, polygon, .32, .55, .20)
        if abs(score['score'] - report['score']) > 1e-6:
            raise ValueError('alignment report and output transform disagree')
        candidates.append({'id': name, 'eligible': quality(report), 'report': report,
                           'trail': original.evenly_subsample(mapped, 600).tolist()})
    # Follow the final ZIP default for reconstruction maps. Polygon fits remain diagnostics.
    result = {'candidates': candidates, 'recommended': 'grid',
              'needsReview': True,
              'message': '自动对齐完成，请核对轮廓和移动方向后启用；对称场地可能存在方向歧义'}
    write(root / 'result.json', result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    run(parser.parse_args().job.resolve())
