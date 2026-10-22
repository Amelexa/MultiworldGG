import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from install_apworld import inspect_apworld
from zip_safety import UnsafeZipError, validate_zip


class TestInstallApworld(unittest.TestCase):
    def test_accepts_well_formed_apworld(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.apworld"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("example/__init__.py", "")
                archive.writestr("example/archipelago.json", json.dumps({"game": "Example"}))
            package, manifest = inspect_apworld(path)
            self.assertEqual(package, "example")
            self.assertEqual(manifest["game"], "Example")

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.apworld"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../bad/__init__.py", "")
                archive.writestr("../bad/archipelago.json", '{"game":"Bad"}')
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                inspect_apworld(path)

    def test_rejects_extreme_compression_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bomb.zip"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("payload", b"0" * (2 * 1024 * 1024))
            with zipfile.ZipFile(path) as archive:
                with self.assertRaisesRegex(UnsafeZipError, "compression ratio"):
                    validate_zip(archive)

    def test_rejects_oversized_total(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("one", b"1234")
                archive.writestr("two", b"5678")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaisesRegex(UnsafeZipError, "expands beyond"):
                    validate_zip(archive, max_total_size=7)


if __name__ == "__main__":
    unittest.main()
