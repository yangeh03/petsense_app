import json
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orientation import choose_orientation, compose_uwb_transform, oriented_canvas, rotation
from build import rasterize


class OrientationTests(unittest.TestCase):
    def test_rotated_rectangles_get_horizontal_long_edges_without_changing_distances(self):
        base = np.array([[0, 0], [6, 0], [6, 2], [0, 2]])
        for angle in [-81, -23, 0, 19, 73, 90]:
            with self.subTest(angle=angle):
                polygon = base @ rotation(angle).T + [-3, 9]
                matrix, translation, record = choose_orientation(polygon)
                q = polygon @ matrix.T + translation
                np.testing.assert_allclose(np.ptp(q, axis=0), [6, 2], atol=1e-10)
                np.testing.assert_allclose(np.linalg.norm(np.roll(q, -1, axis=0)-q, axis=1), [6, 2, 6, 2], atol=1e-10)
                self.assertEqual(record['method'], 'dominant-long-edges')
                self.assertFalse(record['ambiguous'])

    def test_concave_shape_is_preserved_and_square_allows_manual_quarter_turn(self):
        base = np.array([[0, 0], [6, 0], [6, 1], [2, 1], [2, 4], [0, 4]])
        polygon = base @ rotation(27).T
        matrix, translation, record = choose_orientation(polygon)
        q = polygon @ matrix.T + translation
        np.testing.assert_allclose(q[3] - q[0], [2, 1], atol=1e-10)
        self.assertEqual(q.shape, polygon.shape)
        square = [[0, 0], [2, 0], [2, 2], [0, 2]]
        _, _, square_record = choose_orientation(square, extra_degrees=90)
        self.assertTrue(square_record['ambiguous'])
        self.assertAlmostEqual(square_record['rotationDeg'], 90)
        _, _, manual = choose_orientation(base, mode='none', extra_degrees=15)
        self.assertEqual(manual['rotationDeg'], 15)

    def test_real_map_uses_wall_edges_rather_than_its_already_axis_aligned_outer_box(self):
        source = ROOT.parents[1] / 'server/petsense/uwb/assets/reconstruction-room-v1/map.json'
        polygon = json.loads(source.read_text())['polygon']
        _, _, record = choose_orientation(polygon)
        self.assertEqual(record['method'], 'dominant-long-edges')
        self.assertTrue(-13 < record['rotationDeg'] < -7)
        self.assertGreater(record['wallCoherence'], .9)

    def test_transform_composition_keeps_raw_uwb_anchors_and_known_points_consistent(self):
        old = {'scale': 2, 'matrix': [[-1, 0], [0, 1]], 'translation': [-3, 4]}
        r = rotation(-31)
        d = np.array([2, -1])
        new = compose_uwb_transform(old, r, d)
        for anchor in [[0, 0], [4, 0], [0, 4], [1.25, 2.5]]:
            p = np.asarray(anchor)
            expected = r @ (old['scale'] * np.asarray(old['matrix']) @ p + old['translation']) + d
            got = new['scale'] * np.asarray(new['matrix']) @ p + new['translation']
            np.testing.assert_allclose(got, expected, atol=1e-12)
        self.assertEqual(new['scale'], old['scale'])
        self.assertLess(np.linalg.det(new['matrix']), 0)  # Existing reflection retained.

    def test_rotated_canvas_contains_all_corners_and_colored_points_land_at_correct_pixels(self):
        low, extent, resolution = np.array([0., 0.]), np.array([2., 1.]), .1
        polygon = [[0, 0], [2, 0], [2, 1], [0, 1]]
        r, d, _ = choose_orientation(polygon, mode='none', extra_degrees=90)
        new_low, new_extent = oriented_canvas(low, extent, resolution, r, d)
        corners = np.asarray(polygon) @ r.T + d
        self.assertTrue((corners >= new_low).all())
        self.assertTrue((corners <= new_low + new_extent).all())
        scene = np.array([[.45, .45, .2], [1.45, .45, .2]])
        colors = np.array([[255, 0, 0], [0, 0, 255]], dtype=np.uint8)
        scene[:, :2] = scene[:, :2] @ r.T + d
        raster = rasterize(scene, colors, new_low, new_extent, resolution, -.05, 1.2)
        pixel = np.floor((scene[:, :2] - new_low) / resolution).astype(int)
        for point, color in zip(pixel, colors):
            np.testing.assert_array_equal(raster[len(raster)-1-point[1], point[0]], color)

    def test_nonorthogonal_outline_falls_back_and_invalid_inputs_fail(self):
        pentagon = [[np.cos(a), np.sin(a)] for a in np.arange(5)*2*np.pi/5]
        _, _, record = choose_orientation(pentagon)
        self.assertEqual(record['method'], 'minimum-area-hull-box')
        self.assertTrue(record['ambiguous'])
        with self.assertRaises(ValueError):
            choose_orientation([[0, 0], [1, 0], [2, 0]])
        with self.assertRaises(ValueError):
            choose_orientation([[0, 0], [1, 0], [0, 1]], extra_degrees=float('nan'))
