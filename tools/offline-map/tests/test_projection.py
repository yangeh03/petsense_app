import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('offline_build', ROOT/'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class ProjectionTests(unittest.TestCase):
    def test_height_crop_removes_ceiling_and_preserves_topmost_retained_color(self):
        scene = np.array([[.5, .5, 0], [.5, .5, .8], [.5, .5, 2.5], [.5, 1.5, .1]])
        colors = np.array([[1, 2, 3], [10, 20, 30], [200, 200, 200], [4, 5, 6]], dtype=np.uint8)
        image = build.rasterize(scene, colors, np.array([0, 0]), np.array([2, 2]), 1, -.05, 1.2)
        np.testing.assert_array_equal(image[1, 0], [10, 20, 30])
        np.testing.assert_array_equal(image[0, 0], [4, 5, 6])  # Map +Y is image -Y.
        np.testing.assert_array_equal(image[0, 1], [248, 248, 248])

    def test_binary_reader_preserves_xyzrgb_and_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'cloud.ply'
            header = ('ply\nformat binary_little_endian 1.0\nelement vertex 1\n'
                      'property float x\nproperty float y\nproperty float z\n'
                      'property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n').encode()
            p.write_bytes(header + struct.pack('<fffBBB', 1, 2, 3, 4, 5, 6))
            xyz, rgb = build.read_binary_xyzrgb(p)
            np.testing.assert_array_equal(xyz, [[1, 2, 3]])
            np.testing.assert_array_equal(rgb, [[4, 5, 6]])
            p.write_bytes(header)
            with self.assertRaises(ValueError):
                build.read_binary_xyzrgb(p)
            p.write_bytes(header.replace(b'property float x', b'property double x') + struct.pack('<fffBBB', 1, 2, 3, 4, 5, 6))
            with self.assertRaisesRegex(ValueError, 'XYZ float32'):
                build.read_binary_xyzrgb(p)

    def test_raster_size_guard(self):
        with self.assertRaises(ValueError):
            build.rasterize(np.zeros((1, 3)), np.zeros((1, 3)), np.zeros(2), np.array([100, 100]), .001, 0, 1)
