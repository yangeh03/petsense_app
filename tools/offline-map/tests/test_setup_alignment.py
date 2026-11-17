import hashlib
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import setup_align as adapter


class SetupAlignmentTests(unittest.TestCase):
    def test_sparse_polygon_cleaning_does_not_block_raw_grid_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'job.json').write_text(json.dumps({'mapId': 'room', 'version': 'v1', 'walkId': 'walk'}))
            with (root / 'trail.csv').open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['x_m', 'y_m', 'raw_x_m', 'raw_y_m', 'valid_fix', 'pos_updated'])
                for index in range(45):
                    x = index * .01 if index < 8 else 3 + index * .02
                    writer.writerow([x, 0, x, 0, 1, 1])

            def fake_build(*args, **kwargs):
                (root / 'native').mkdir()
                (root / 'native/map.json').write_text(json.dumps({'polygon': [[0, 0], [4, 0], [4, 3]]}))

            grid = {'id': 'grid', 'eligible': False, 'report': {}, 'trail': []}
            with patch.object(adapter.subprocess, 'run', side_effect=fake_build) as commands, \
                 patch.object(adapter, 'grid_fit') as fit, \
                 patch.object(adapter, 'extra_candidate', return_value=grid):
                adapter.run(root / 'job.json')
            self.assertEqual(len(fit.call_args.args[0]), 45)
            self.assertEqual(commands.call_count, 1)  # Only building, no polygon subprocess.
            result = json.loads((root / 'result.json').read_text())
            self.assertEqual(result['recommended'], 'grid')
            self.assertEqual(result['candidates'], [grid])
            self.assertIn('暂不能启用', result['message'])
            self.assertIn('45 个原始有效点', result['warnings'][0])

    def test_proven_algorithms_are_byte_identical_to_user_zip_sources(self):
        sources = {
            'uwb_polygon_boundary_align.py': '823209f09bd7e5a3432d6e58062110b77674f7115afabecfc7f919284d4149f2',
            'rebuild_benchmark_auto_alignment.py': '7d943b8a7d8359d54c3d56dcabdf1df25082abd742edfed1c12c8e9caf24e0a9',
            'rebuild_evaluate_obstacle_alignment.py': 'fc5809b1b1e4a1fd1770d98009f018bbf79e741b43b8a58bc5df064656c8411d',
        }
        for name, expected in sources.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
    def test_final_frame_composition_preserves_reflection_and_scale(self):
        rotation = np.array([[0, -1], [1, 0]])
        mirror = np.array([[-1, 0], [0, 1]])
        source = (mirror, np.array([3, -2]), 1.1)
        manifest = {'provenance': {'orientation': {'matrix': rotation.tolist(), 'translationM': [7, 4]}}}
        final = adapter.floor_to_map(source, manifest)
        point = np.array([[2, 5]])
        expected = adapter.benchmark.apply(point, *source) @ rotation.T + [7, 4]
        np.testing.assert_allclose(adapter.benchmark.apply(point, *final), expected)
        self.assertLess(np.linalg.det(final[0]), 0)

    def test_grid_search_matches_zip_default_without_manual_ground_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'diagnostics').mkdir()
            (root / 'confirmed-floor.json').write_text(json.dumps({'scale_prior': 2}))
            obstacles = np.zeros((20, 20), dtype=bool)
            obstacles[[0, -1], :] = True
            obstacles[:, [0, -1]] = True
            np.savez(root / 'diagnostics/navigation_grid.npz', occupied_raw=obstacles,
                     low=[0, 0], cell_reconstruction_units=.1)
            raw = np.array([[.1, .1], [.8, .1], [.8, .8], [.1, .8]])
            params = np.array([0, .9, 0, 1.05])
            with patch.object(adapter.optimize, 'differential_evolution', return_value=SimpleNamespace(x=params)) as search:
                matrix, translation, scale = adapter.grid_fit(raw, root, root)
            kwargs = search.call_args.kwargs
            self.assertEqual(kwargs['bounds'][-1], (.85, 1.15))
            self.assertEqual(kwargs['seed'], 20260730)
            self.assertEqual(kwargs['maxiter'], 45)
            self.assertEqual(kwargs['popsize'], 8)
            self.assertTrue(np.isfinite(search.call_args.args[0](params)))
            np.testing.assert_array_equal(matrix, [[-1, 0], [0, 1]])
            np.testing.assert_allclose(translation, [.9, 0])
            self.assertEqual(scale, 1.05)

    def test_invalid_manual_shear_is_rejected_before_reading_grid(self):
        with self.assertRaises(ValueError):
            adapter.extra_candidate('manual', (np.array([[1, 1], [0, 1]]), np.zeros(2), 1),
                                    np.ones((10, 2)), Path('unused'), Path('unused'), {}, {}, True)

    def test_polygon_diagnostics_do_not_accept_low_coverage(self):
        report = {'inlier_ratio': .8, 'boundary_coverage_ratio': .3,
                  'median_edge_error_m': .07, 'outside_far_ratio': .05}
        self.assertFalse(adapter.quality(report))
