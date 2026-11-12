"""Compile and run the actual GNSS parser/AT scheduler on the host."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class GnssTests(unittest.TestCase):
    def test_runtime_wire_and_invalid_coordinate_suppression(self):
        for sketch in ('petsense_esp32', 'edge_firmware'):
            with self.subTest(sketch=sketch), tempfile.TemporaryDirectory() as tmp:
                binary = str(Path(tmp) / 'gnss-runtime')
                subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                '-I', str(ROOT / 'firmware' / sketch),
                                str(ROOT / 'firmware/tests/gnss_runtime_test.cpp'),
                                str(ROOT / 'firmware' / sketch / 'gnss_protocol.cpp'),
                                '-o', binary], check=True)
                for mode in ('fixed', 'empty', 'stale'):
                    value = json.loads(subprocess.check_output([binary, mode], text=True))
                    self.assertTrue(value['health']['valid'])
                    self.assertEqual(value['gps']['valid'], mode == 'fixed')
                    if mode == 'fixed':
                        self.assertAlmostEqual(value['gps']['latitude'], 31.2223881)
                        self.assertAlmostEqual(value['gps']['speedMps'], 5.144, places=3)
                    else:
                        self.assertIsNone(value['gps']['latitude'])
                        self.assertIsNone(value['gps']['longitude'])

    def test_parser_and_scheduler(self):
        for sketch in ('petsense_esp32', 'edge_firmware'):
            with self.subTest(sketch=sketch), tempfile.TemporaryDirectory() as tmp:
                binary = str(Path(tmp) / 'gnss-test')
                subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                '-I', str(ROOT / 'firmware' / sketch),
                                str(ROOT / 'firmware/tests/gnss_protocol_test.cpp'),
                                str(ROOT / 'firmware' / sketch / 'gnss_protocol.cpp'),
                                '-o', binary], check=True)
                subprocess.run([binary], check=True)

    def test_standalone_sketch_copies_match(self):
        for name in ('gnss_protocol.h', 'gnss_protocol.cpp', 'gnss_runtime.h'):
            self.assertEqual((ROOT / 'firmware/edge_firmware' / name).read_bytes(),
                             (ROOT / 'firmware/petsense_esp32' / name).read_bytes())
