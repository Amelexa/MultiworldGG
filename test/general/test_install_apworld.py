import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from install_apworld import inspect_apworld


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


if __name__ == "__main__":
    unittest.main()
