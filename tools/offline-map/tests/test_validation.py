import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validate import validate_bundle
spec = importlib.util.spec_from_file_location('checked_build', ROOT / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
ASSET = ROOT.parents[1] / 'server/petsense/uwb/assets/reconstruction-room-v1'


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'map'
        shutil.copytree(ASSET, self.bundle)
        self.manifest = self.bundle / 'map.json'
        self.data = json.loads(self.manifest.read_text())

    def test_confirmed_map_passes_shared_contract_and_full_png_decode(self):
        value = validate_bundle(self.bundle)
        self.assertEqual(value.public()['background']['crc32'], '7d2cc7ad')

    def test_bad_calibration_dimensions_paths_and_polygon_are_rejected(self):
        for kind in ['anchors', 'matrix', 'scale', 'nan', 'dimensions', 'path', 'outside']:
            with self.subTest(kind=kind):
                d = copy.deepcopy(self.data)
                if kind == 'anchors':
                    d['anchors'][2]['uwb'] = d['anchors'][1]['uwb']
                elif kind == 'matrix':
                    d['uwbTransform']['matrix'] = [[1, 1], [1, 1]]
                elif kind == 'scale':
                    d['uwbTransform']['scale'] = 0
                elif kind == 'nan':
                    d['polygon'][0][0] = float('nan')
                elif kind == 'dimensions':
                    d['background']['width'] += 1
                elif kind == 'path':
                    d['background']['file'] = '../private.png'
                else:
                    d['polygon'][0][0] = 100
                self.manifest.write_text(json.dumps(d))
                with self.assertRaises((ValueError, OSError)):
                    validate_bundle(self.bundle)

    def test_png_payload_corruption_is_detected_even_with_valid_header(self):
        p = self.bundle / 'floorplan.png'
        damaged = bytearray(p.read_bytes())
        damaged[100] ^= 1
        p.write_bytes(damaged)
        with self.assertRaises((ValueError, OSError, SyntaxError)):
            validate_bundle(self.bundle)

    def test_orientation_calibration_canvas_and_sidecar_are_checked_together(self):
        from orientation import choose_orientation
        _, _, record = choose_orientation(self.data['polygon'], mode='none')
        record['sourceUwbTransform'] = copy.deepcopy(self.data['uwbTransform'])
        bg = self.data['background']
        record['sourceBackground'] = {'origin': bg['origin'],
            'extent': [bg['width'] * bg['metersPerPixel'], bg['height'] * bg['metersPerPixel']]}
        self.data['provenance']['orientation'] = record
        self.manifest.write_text(json.dumps(self.data))
        validate_bundle(self.bundle)
        for kind in ['transform', 'matrix', 'canvas', 'sidecar']:
            with self.subTest(kind=kind):
                d = copy.deepcopy(self.data)
                if kind == 'transform':
                    d['uwbTransform']['translation'][0] += 1
                elif kind == 'matrix':
                    d['provenance']['orientation']['matrix'][0][1] = 1
                elif kind == 'canvas':
                    d['provenance']['orientation']['sourceBackground']['extent'][0] += 10
                else:
                    (self.bundle / 'orientation.json').write_text('{}')
                self.manifest.write_text(json.dumps(d))
                with self.assertRaises(ValueError):
                    validate_bundle(self.bundle)

    def arguments(self):
        floor = {'scale_prior': 1, 'floor_origin_3d': [0, 0, 0],
                 'floor_axis_x_3d': [1, 0, 0], 'floor_axis_y_3d': [0, 1, 0],
                 'floor_up_normal_3d': [0, 0, 1]}
        anchors = {'anchors_xyz_m': {'A0': [0, 0, 0], 'A1': [4, 0, 0], 'A2': [0, 4, 0]}}
        alignment = {'scale': 1, 'rotation_matrix': [[1, 0], [0, 1]], 'translation_m': [0, 0]}
        for name, data in [('floor', floor), ('anchors', anchors), ('alignment', alignment)]:
            (self.root / f'{name}.json').write_text(json.dumps(data))
        return SimpleNamespace(output=self.root / 'exported', map_id='room', version='v2',
                               anchor_indices=[0, 1, 2], min_height=-.05, max_height=1.2,
                               obstacle_height=1, resolution=.01, floor=self.root / 'floor.json',
                               anchors=self.root / 'anchors.json', alignment=self.root / 'alignment.json',
                               pointcloud=self.root / 'missing.ply', name='room')

    def test_invalid_input_calibration_fails_before_reading_pointcloud(self):
        args = self.arguments()
        args.alignment.write_text(json.dumps({'scale': 0, 'rotation_matrix': [[1, 0], [0, 1]], 'translation_m': [0, 0]}))
        with patch.object(build, 'read_binary_xyzrgb') as reader:
            with self.assertRaises(ValueError):
                build.build(args)
            reader.assert_not_called()
        self.assertFalse(args.output.exists())

    def test_failed_or_invalid_export_leaves_no_partial_published_directory(self):
        args = self.arguments()
        def bad_export(args, staging, *rest):
            (staging / 'partial.txt').write_text('partial')
            raise ValueError('bad input')
        with patch.object(build, 'export', side_effect=bad_export):
            with self.assertRaises(ValueError):
                build.build(args)
        self.assertFalse(args.output.exists())
        self.assertEqual(list(self.root.glob('.exported-*')), [])

    def test_success_is_published_only_after_validation_and_existing_output_is_preserved(self):
        args = self.arguments()
        def valid_export(args, staging, *rest):
            shutil.copytree(self.bundle, staging, dirs_exist_ok=True)
        with patch.object(build, 'export', side_effect=valid_export):
            build.build(args)
            validate_bundle(args.output)
            with self.assertRaises(ValueError):
                build.build(args)
        self.assertEqual((args.output / 'floorplan.png').read_bytes(), (self.bundle / 'floorplan.png').read_bytes())
