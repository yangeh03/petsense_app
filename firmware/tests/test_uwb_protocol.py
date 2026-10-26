"""Host-side wire/parser regression; this does not replace an ESP32 build."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def compile_parser(output, device_id=None):
    output = Path(output)
    # Never create credentials or generated files in the sketch directory.
    (output.parent / 'secrets.h').write_text(
        '#define PETSENSE_MQTT_PASSWORD "HOST_TEST_ONLY_NOT_A_CREDENTIAL"\n')
    # The board header also declares unrelated modem/audio string constants.
    command = ['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable',
               '-I', str(output.parent), '-I', str(ROOT / 'firmware/tests/include'),
               '-I', str(ROOT / 'firmware/petsense_esp32')]
    if device_id:
        command.append(f'-DPETSENSE_DEVICE_ID="{device_id}"')
    subprocess.run(command + [str(ROOT / 'firmware/tests/uwb_protocol_test.cpp'),
                              str(ROOT / 'firmware/petsense_esp32/uwb_protocol.cpp'),
                              '-o', str(output)], check=True)


class UwbProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.holder.cleanup)
        cls.binary = Path(cls.holder.name) / 'uwb-test'
        compile_parser(cls.binary)

    def run_case(self, name):
        return subprocess.check_output([str(self.binary), name], text=True)

    def test_every_split_boundary(self):
        self.run_case('split')

    def test_per_anchor_kalman_fallback(self):
        self.run_case('distances')

    def test_stale_boundary_and_uptime_wrap(self):
        self.run_case('stale')

    def test_noise_invalid_frames_and_recovery(self):
        self.run_case('recovery')

    def test_latest_tag_and_duplicate_behavior(self):
        self.run_case('latest')

    def test_identity_and_distinct_uart_pins(self):
        self.run_case('identity')

    def test_compact_contract(self):
        data = json.loads(self.run_case('contract'))
        expected = json.loads((ROOT / 'server/tests/fixtures/uwb_wire.json').read_text())
        self.assertEqual(data, expected)

    def test_custom_device_id_updates_both_topics(self):
        binary = Path(self.holder.name) / 'uwb-custom'
        compile_parser(binary, 'collar-test-002')
        data = json.loads(subprocess.check_output([str(binary), 'contract'], text=True))
        self.assertEqual(data['deviceId'], 'collar-test-002')
        self.assertEqual(data['topic'], 'petsense/sensor/collar-test-002')
        self.assertEqual(data['commandTopic'], 'petsense/device/collar-test-002/command')
