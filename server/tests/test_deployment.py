import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from deploy_server import MANAGED_FILES, activate, check_health
from receive import extract_archive


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "app"
        self.release = self.base / "release"
        self.system = self.base / "system"
        self.root.mkdir()
        self.system.mkdir()
        for name in MANAGED_FILES:
            path = self.release / "server" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == "petsense":
                (path / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
                (path / "__init__.py").write_text("new")
            else:
                path.write_text("new")
        (self.root / "petsense").mkdir()
        (self.root / "petsense" / "__init__.py").write_text("old")
        (self.root / "collection_data").mkdir()
        (self.root / "collection_data" / "collection.sqlite3").write_bytes(b"real-data")
        self.calls = []

    def command(self, args, **kwargs):
        self.calls.append(args)

    def test_failed_health_restores_code_interpreter_and_preserves_data(self):
        dropin = self.system / "90-petsense-deploy.conf"
        dropin.write_text("old interpreter")
        def fail():
            raise RuntimeError("health failed")
        with self.assertRaisesRegex(RuntimeError, "health failed"):
            activate(self.release, "a" * 40, self.root, self.system, self.command, fail)
        self.assertEqual((self.root / "petsense" / "__init__.py").read_text(), "old")
        self.assertFalse((self.root / "wsgi.py").exists())
        self.assertEqual(dropin.read_text(), "old interpreter")
        self.assertEqual((self.root / "collection_data" / "collection.sqlite3").read_bytes(), b"real-data")
        self.assertFalse((self.root / ".deployed-sha").exists())
        self.assertEqual(sum(c == ["sudo", "systemctl", "restart", "petsense"] for c in self.calls), 2)

    def test_success_records_revision_and_preserves_unmanaged_files(self):
        (self.root / "dashboard.html").write_text("cloud dashboard")
        activate(self.release, "b" * 40, self.root, self.system, self.command, lambda: None)
        self.assertEqual((self.root / ".deployed-sha").read_text().strip(), "b" * 40)
        self.assertEqual((self.root / "dashboard.html").read_text(), "cloud dashboard")
        self.assertEqual((self.root / "petsense" / "__init__.py").read_text(), "new")

    def test_incomplete_bundle_does_not_modify_live_code(self):
        (self.release / "server" / "wsgi.py").unlink()
        with self.assertRaises(FileNotFoundError):
            activate(self.release, "c" * 40, self.root, self.system, self.command, lambda: None)
        self.assertEqual((self.root / "petsense" / "__init__.py").read_text(), "old")
        self.assertEqual(self.calls, [])

    def test_model_absent_from_release_reuses_live_copy(self):
        (self.root / "models").mkdir()
        (self.root / "models" / "random_forest_1s.joblib").write_text("live-model")
        (self.release / "server" / "models" / "random_forest_1s.joblib").unlink()
        activate(self.release, "d" * 40, self.root, self.system, self.command, lambda: None)
        self.assertEqual((self.root / ".deployed-sha").read_text().strip(), "d" * 40)
        # 现网模型未被触碰
        self.assertEqual((self.root / "models" / "random_forest_1s.joblib").read_text(), "live-model")
        # 根目录没有现网模型时（全新机器），缺模型的包必须报错而不是静默部署
        (self.root / "models" / "random_forest_1s.joblib").unlink()
        with self.assertRaises(FileNotFoundError):
            activate(self.release, "e" * 40, self.root, self.system, self.command, lambda: None)

    def test_receiver_rejects_traversal_and_symlinks(self):
        for name, link in (("server/../../escape", False), ("server/link", True)):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                info = tarfile.TarInfo(name)
                if link:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/etc/passwd"
                archive.addfile(info)
            stream.seek(0)
            with self.assertRaises(ValueError):
                extract_archive(stream, self.base / "extract")
        self.assertFalse((self.base / "escape").exists())

    def test_health_rejects_missing_or_empty_collection_credentials(self):
        token = self.base / "token"
        with self.assertRaises(FileNotFoundError):
            check_health(token)
        token.write_text("\n")
        with self.assertRaisesRegex(RuntimeError, "credential is empty"):
            check_health(token)


if __name__ == "__main__":
    unittest.main()
