from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from open_shift.package import PackageError, build_mod_package


class PackageTests(unittest.TestCase):
    def test_source_only_package_contains_install_and_launch_entries(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "open-shift.zip"
            result = build_mod_package(project_root=root, output=output)
            self.assertEqual(result.forbidden_entries, ())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("PACKAGE_MANIFEST.json", names)
                self.assertIn("packaging/install-isolated-copy.ps1", names)
                self.assertIn("packaging/launch-open-shift.ps1", names)
                self.assertIn("packaging/launch-deepseek-acceptance.ps1", names)
                self.assertIn("src/open_shift/package.py", names)
                self.assertFalse(any(name.startswith("game-patch/assets/news/") for name in names))
                self.assertFalse(any("reference-local" in name for name in names))
                manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
                self.assertTrue(manifest["source_only"])
                self.assertFalse(manifest["contains_original_data_win"])

    def test_package_rejects_output_inside_project_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(PackageError, "inside"):
            build_mod_package(project_root=root, output=root / "src" / "bad.zip")


if __name__ == "__main__":
    unittest.main()
