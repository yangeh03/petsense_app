import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class EdgeUwbRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.holder.cleanup)
        cls.binary = Path(cls.holder.name) / 'edge-uwb'
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-variable', '-I', str(ROOT / 'firmware/tests/include'),
                        '-I', str(ROOT / 'firmware/edge_firmware'),
                        str(ROOT / 'firmware/tests/edge_uwb_runtime_test.cpp'),
                        str(ROOT / 'firmware/edge_firmware/uwb_protocol.cpp'),
                        '-o', str(cls.binary)], check=True)

    def snapshot(self, mode):
        return json.loads(subprocess.check_output([str(self.binary), mode], text=True))

    def test_uart_packet_becomes_named_uwb_fields_without_changing_action(self):
        frame = self.snapshot('valid')
        self.assertEqual(frame['action'], {'label': 'standing'})
        self.assertEqual(frame['uwb'], {
            'valid': True, 'receivedMs': 100, 'timer': 123, 'tagId': 7, 'anchorId': 2,
            'sequence': 31, 'anchorMask': 7, 'kalmanEnabled': False,
            'distanceMm': [1000, 2000, 3000, 0, 0, 0, 0, 0],
            'rawMm': [1000, 2000, 3000, 0, 0, 0, 0, 0], 'kalmanMm': [0] * 8})

    def test_missing_and_disconnected_uwb_are_invalid(self):
        self.assertFalse(self.snapshot('empty')['uwb']['valid'])
        self.assertFalse(self.snapshot('stale')['uwb']['valid'])
        self.assertTrue(self.snapshot('boundary')['uwb']['valid'])

    def test_clock_wrap_remains_fresh(self):
        self.assertTrue(self.snapshot('wrap')['uwb']['valid'])
