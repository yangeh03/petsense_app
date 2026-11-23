"""W10 Agent 录音协议 host 回归：编译并运行两目标共享的 agent_audio_protocol 实现。"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class AgentAudioProtocolTests(unittest.TestCase):
    def test_protocol_logic_both_sketches(self):
        for sketch in ('petsense_esp32', 'edge_firmware'):
            with self.subTest(sketch=sketch), tempfile.TemporaryDirectory() as tmp:
                binary = str(Path(tmp) / 'agent-audio-test')
                subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                '-I', str(ROOT / 'firmware' / sketch),
                                str(ROOT / 'firmware/tests/agent_audio_test.cpp'),
                                str(ROOT / 'firmware' / sketch / 'agent_audio_protocol.cpp'),
                                '-o', binary], check=True)
                subprocess.run([binary], check=True)

    def test_standalone_sketch_copies_match(self):
        for name in ('agent_audio_protocol.h', 'agent_audio_protocol.cpp'):
            self.assertEqual((ROOT / 'firmware/edge_firmware' / name).read_bytes(),
                             (ROOT / 'firmware/petsense_esp32' / name).read_bytes())


if __name__ == '__main__':
    unittest.main()
