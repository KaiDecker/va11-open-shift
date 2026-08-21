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
                self.assertIn("packaging/install-open-shift.ps1", names)
                self.assertIn("packaging/configure-api-key.ps1", names)
                self.assertIn("packaging/build-player-release.ps1", names)
                self.assertIn("packaging/launch-open-shift.ps1", names)
                self.assertIn("packaging/launch-deepseek-acceptance.ps1", names)
                self.assertIn("packaging/uninstall-open-shift.ps1", names)
                self.assertIn("packaging/INSTALL.md", names)
                self.assertIn("src/open_shift/package.py", names)
                self.assertFalse(any(name.startswith("game-patch/assets/news/") for name in names))
                self.assertFalse(any("reference-local" in name for name in names))
                manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
                self.assertTrue(manifest["source_only"])
                self.assertFalse(manifest["contains_original_data_win"])
                self.assertTrue(manifest["requires_python"])

    def test_optional_release_tools_use_non_original_archive_names(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "OpenShift.exe"
            gui = Path(temp_dir) / "OpenShiftSetup.exe"
            icon = Path(temp_dir) / "OpenShift.ico"
            utmt = Path(temp_dir) / "utmt.zip"
            runtime.write_bytes(b"runtime")
            gui.write_bytes(b"gui")
            icon.write_bytes(b"icon")
            utmt.write_bytes(b"utmt")
            output = Path(temp_dir) / "release.zip"
            build_mod_package(
                project_root=root,
                output=output,
                runtime_exe=runtime,
                gui_exe=gui,
                icon=icon,
                utmt_cli=utmt,
            )
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("OpenShift.exe", names)
                self.assertIn("OpenShiftSetup.exe", names)
                self.assertIn("OpenShift.ico", names)
                self.assertIn("tools/utmt/UndertaleModCli.zip", names)
                self.assertFalse(any(name.startswith("reference-local/") for name in names))
                manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
                self.assertFalse(manifest["source_only"])
                self.assertTrue(manifest["contains_runtime_exe"])
                self.assertTrue(manifest["contains_gui_exe"])
                self.assertTrue(manifest["contains_icon"])
                self.assertTrue(manifest["contains_utmt_cli"])

    def test_package_rejects_output_inside_project_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(PackageError, "inside"):
            build_mod_package(project_root=root, output=root / "src" / "bad.zip")

    def test_player_install_contract_does_not_target_steam_or_ship_secrets(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "install-open-shift.ps1").read_text(encoding="utf-8")
        uninstaller = (root / "packaging" / "uninstall-open-shift.ps1").read_text(encoding="utf-8")
        self.assertIn("validate-patch-target", installer)
        self.assertIn("GameCopyDir must differ", installer)
        low_level = (root / "packaging" / "install-isolated-copy.ps1").read_text(encoding="utf-8")
        self.assertIn("install-patch", low_level)
        self.assertNotIn("OPEN_SHIFT_API_KEY =", installer)
        self.assertIn("uninstall-patch", uninstaller)
        self.assertIn("$RemoveSaves", uninstaller)
        self.assertIn("$WaitForProcessId", uninstaller)
        self.assertIn("Steam installation was not modified", uninstaller)
        self.assertIn("System.Security.Cryptography.ProtectedData", installer)
        self.assertIn("api-key.dpapi", installer)
        self.assertIn("Expand-Archive", installer)
        self.assertIn("UndertaleModCli.zip", installer)
        self.assertIn("OpenShiftSetup.exe", installer)
        self.assertIn("OpenShift.ico", installer)
        self.assertIn("$CompletionMarker", installer)
        self.assertIn("exit 0", installer)
        self.assertIn('Join-Path $packageRoot "tools"', installer)
        self.assertIn("$sameRoot", installer)
        self.assertIn("$link.TargetPath = $installedGui", installer)
        configure = (root / "packaging" / "configure-api-key.ps1").read_text(encoding="utf-8")
        self.assertIn("System.Security.Cryptography.ProtectedData", configure)
        self.assertIn("Add-Type -AssemblyName System.Security", configure)

    def test_gui_exposes_safe_player_workflow(self) -> None:
        root = Path(__file__).resolve().parents[1]
        gui = (root / "packaging" / "open-shift-gui.ps1").read_text(encoding="utf-8")
        self.assertIn("安装 / 修复", gui)
        self.assertIn("准备并启动", gui)
        self.assertIn("Steam 原版 data.win：只读输入", gui)
        self.assertIn("Save-ApiKey", gui)
        self.assertIn("launcher.log", gui)
        self.assertIn("libraryfolders.vdf", gui)
        self.assertIn("Complete-Log", gui)
        self.assertIn("$script:lastLog", gui)
        self.assertNotIn("Safe installer and launcher", gui)
        self.assertIn("$form.Icon", gui)
        self.assertIn("WaitForExit", gui)
        self.assertIn("Refresh()", gui)
        self.assertIn("$installCompleted", gui)
        self.assertIn("$script:launchConfirmed", gui)
        self.assertIn("Entering main loop", gui)
        self.assertIn("游戏已启动，可以关闭此窗口。", gui)

    def test_optional_release_tools_use_fixed_non_original_names(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "OpenShift.exe"
            utmt = Path(temp_dir) / "utmt.zip"
            runtime.write_bytes(b"runtime")
            utmt.write_bytes(b"utmt")
            output = Path(temp_dir) / "release.zip"
            build_mod_package(
                project_root=root,
                output=output,
                runtime_exe=runtime,
                utmt_cli=utmt,
            )
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("OpenShift.exe", names)
                self.assertIn("tools/utmt/UndertaleModCli.zip", names)
                self.assertFalse(any(name.startswith("reference-local/") for name in names))

    def test_gui_without_runtime_still_reports_python_requirement(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            gui = Path(temp_dir) / "OpenShiftSetup.exe"
            gui.write_bytes(b"gui")
            output = Path(temp_dir) / "gui-source.zip"
            build_mod_package(project_root=root, output=output, gui_exe=gui)
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
                self.assertTrue(manifest["source_only"])
                self.assertTrue(manifest["requires_python"])


if __name__ == "__main__":
    unittest.main()
