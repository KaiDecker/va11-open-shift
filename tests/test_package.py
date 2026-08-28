from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from open_shift.package import PackageError, _validate_archive, build_mod_package


class PackageTests(unittest.TestCase):
    def test_source_only_package_contains_install_and_launch_entries(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "open-shift.zip"
            result = build_mod_package(project_root=root, output=output)
            self.assertEqual(result.forbidden_entries, ())
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                names = set(archive.namelist())
                self.assertIn("PACKAGE_MANIFEST.json", names)
                self.assertIn("packaging/install-isolated-copy.ps1", names)
                self.assertIn("packaging/install-open-shift.ps1", names)
                self.assertIn("packaging/webview/index.html", names)
                self.assertIn("packaging/configure-api-key.ps1", names)
                self.assertIn("packaging/launch-open-shift.ps1", names)
                self.assertIn("packaging/uninstall-open-shift.ps1", names)
                self.assertIn("assets/open-shift-icon.svg", names)
                self.assertNotIn("README.md", names)
                self.assertNotIn("CONTRIBUTING.md", names)
                self.assertNotIn("packaging/README.md", names)
                self.assertNotIn("packaging/INSTALL.md", names)
                self.assertNotIn("game-patch/README.md", names)
                self.assertNotIn("assets/screenshots/bar-stella-order.png", names)
                self.assertNotIn("assets/screenshots/jill-room-day-2.png", names)
                self.assertNotIn("assets/screenshots/chinese-installer.png", names)
                self.assertIn("src/open_shift/package.py", names)
                self.assertNotIn("packaging/build-player-release.ps1", names)
                self.assertNotIn("packaging/launch-deepseek-acceptance.ps1", names)
                self.assertNotIn("packaging/open-shift-gui.ps1", names)
                self.assertNotIn("packaging/gui_launcher.cs", names)
                self.assertNotIn("packaging/OpenShiftSetup.csproj", names)
                self.assertNotIn("packaging/runtime_entry.py", names)
                self.assertNotIn("packaging/create-icon.ps1", names)
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
            delta = Path(temp_dir) / "data-win.delta"
            runtime.write_bytes(b"runtime")
            gui.write_bytes(b"gui")
            icon.write_bytes(b"icon")
            delta.write_bytes(b"delta")
            output = Path(temp_dir) / "release.zip"
            build_mod_package(
                project_root=root,
                output=output,
                runtime_exe=runtime,
                gui_exe=gui,
                icon=icon,
                data_delta=delta,
            )
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                names = set(archive.namelist())
                self.assertIn("OpenShift.exe", names)
                self.assertIn("OpenShiftSetup.exe", names)
                self.assertIn("OpenShift.ico", names)
                self.assertIn("patch/data-win.delta", names)
                self.assertFalse(any(name.startswith("reference-local/") for name in names))
                manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
                self.assertFalse(manifest["source_only"])
                self.assertTrue(manifest["contains_runtime_exe"])
                self.assertTrue(manifest["contains_gui_exe"])
                self.assertTrue(manifest["contains_icon"])
                self.assertFalse(manifest["contains_utmt_cli"])
                self.assertTrue(manifest["contains_data_delta"])

    def test_package_rejects_output_inside_project_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(PackageError, "inside"):
            build_mod_package(project_root=root, output=root / "src" / "bad.zip")

    def test_package_validation_rejects_truncated_archive(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "open-shift.zip"
            build_mod_package(project_root=root, output=output)
            truncated = Path(temp_dir) / "truncated.zip"
            truncated.write_bytes(output.read_bytes()[:-32])
            with self.assertRaisesRegex(PackageError, "archive validation"):
                _validate_archive(truncated, ("README.md",))

    def test_player_install_contract_does_not_target_steam_or_ship_secrets(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "install-open-shift.ps1").read_text(encoding="utf-8")
        uninstaller = (root / "packaging" / "uninstall-open-shift.ps1").read_text(encoding="utf-8")
        self.assertIn("validate-patch-target", installer)
        self.assertIn("GameCopyDir must differ", installer)
        low_level = (root / "packaging" / "install-isolated-copy.ps1").read_text(encoding="utf-8")
        self.assertIn("install-patch", low_level)
        self.assertIn("New-InstanceLink", low_level)
        self.assertIn("ItemType Junction", low_level)
        self.assertIn("ItemType HardLink", low_level)
        self.assertIn("ItemType SymbolicLink", low_level)
        self.assertIn('linkType = "copied_file"', low_level)
        self.assertIn("Copy-Item -LiteralPath $Source -Destination $Destination", low_level)
        self.assertIn("refusing to copy the full Steam game", low_level)
        self.assertNotIn("Get-ChildItem -LiteralPath $steamDir -Force | Where-Object", low_level)
        self.assertIn('data_win = "patched-copy"', low_level)
        self.assertIn("open-shift-links.json", low_level)
        self.assertNotIn("OPEN_SHIFT_API_KEY =", installer)
        self.assertIn("uninstall-patch", uninstaller)
        self.assertIn("$RemoveSaves", uninstaller)
        self.assertIn("$WaitForProcessId", uninstaller)
        self.assertIn("shortcut_path", installer)
        self.assertIn('$shortcutPath = ""', installer)
        self.assertNotIn("$link.Save()", installer)
        self.assertIn("$ownsShortcut", uninstaller)
        self.assertIn("was not owned by this installation", uninstaller)
        self.assertIn("Steam installation was not modified", uninstaller)
        self.assertIn("Refusing to remove the Steam game directory", uninstaller)
        self.assertIn("Refusing to remove a junction or symbolic link", uninstaller)
        self.assertIn("Remove links one at a time", uninstaller)
        self.assertIn("System.Security.Cryptography.ProtectedData", installer)
        self.assertIn("api-key.dpapi", installer)
        self.assertIn("OPEN_SHIFT_TIMING_LOG", installer)
        self.assertIn("timing.log", installer)
        self.assertIn("OPEN_SHIFT_DIALOGUE_LOG", installer)
        self.assertIn("prefetch_days = 0", installer)
        self.assertNotIn('"--provider-required"', installer)
        self.assertNotIn("UndertaleModCli", installer)
        self.assertNotIn("Find-UtmtCli", installer)
        self.assertNotIn("UndertaleModCli.zip", installer)
        self.assertNotIn("UtmtCli", low_level)
        self.assertIn("patch\\data-win.delta", installer)
        self.assertIn("apply-data-delta", low_level)
        self.assertIn('OpenShift-" + $safeVersion', installer)
        self.assertIn("PACKAGE_MANIFEST.json", installer)
        self.assertIn("reuseInstalledRoot", installer)
        self.assertIn("OpenShiftSetup.exe", installer)
        self.assertIn("WebView2Loader.dll", installer)
        self.assertIn("OpenShift.ico", installer)
        self.assertIn('"OpenShift-" + $patchFingerprint.Substring(0, 12) + ".ico"', installer)
        self.assertIn("$CompletionMarker", installer)
        self.assertIn("patch_fingerprint", installer)
        self.assertIn("package_version", installer)
        self.assertIn('database = (Join-Path $installRoot "open-shift.sqlite3")', installer)
        self.assertIn('configDir = $installRoot', installer)
        self.assertIn('"--paired-save-dir", (Join-Path `$root "paired-saves")', installer)
        # The launcher script is embedded in a PowerShell here-string, so its
        # runtime variables are escaped in the installer source.
        self.assertIn('`$database = if (`$state.database)', installer)
        self.assertIn(
            '"--runtime-file", (Join-Path `$env:LOCALAPPDATA "VA_11_Hall_A\\open-shift-runtime.ini")',
            installer,
        )
        self.assertNotIn(
            '"--runtime-file", (Join-Path `$root "open-shift-runtime.ini")',
            installer,
        )
        self.assertIn("libraryfolders.vdf", installer)
        self.assertIn("Get-PatchFingerprint", installer)
        self.assertIn("Get-Sha256Hex", installer)
        self.assertIn('game_copy_mode = "patched_data_win_plus_steam_links"', installer)
        self.assertIn("linked_entries", installer)
        self.assertIn("-InstanceId $instanceId", installer)
        self.assertIn("linksReady", installer)
        self.assertNotIn("Get-FileHash", installer)
        self.assertIn('Join-Path $packageRoot "assets"', installer)
        self.assertIn("exit 0", installer)
        self.assertIn("$sameRoot", installer)
        self.assertLess(installer.index("Write-Utf8NoBom $launcher"), installer.index("Write-Host \"Open Shift installed"))
        configure = (root / "packaging" / "configure-api-key.ps1").read_text(encoding="utf-8")
        self.assertIn("System.Security.Cryptography.ProtectedData", configure)
        self.assertIn("Add-Type -AssemblyName System.Security", configure)

    def test_gui_exposes_safe_player_workflow(self) -> None:
        root = Path(__file__).resolve().parents[1]
        host = (root / "packaging" / "native" / "OpenShiftSetup.cpp").read_text(encoding="utf-8")
        webview = (root / "packaging" / "webview" / "index.html").read_text(encoding="utf-8")
        self.assertIn("CryptProtectData", host)
        self.assertIn("CreateDirectoryW(installDir.c_str()", host)
        self.assertIn("CreateCoreWebView2EnvironmentWithOptions", host)
        self.assertIn("CreateCoreWebView2Controller", host)
        self.assertIn("OpenShiftSetup.cpp", (root / "packaging" / "build-player-release.ps1").read_text(encoding="utf-8"))
        self.assertIn("WebView2Loader.dll", (root / "packaging" / "build-player-release.ps1").read_text(encoding="utf-8"))
        self.assertIn("安装 / 修复", webview)
        self.assertIn("准备并启动", webview)
        self.assertIn("导出诊断", webview)
        self.assertIn("检查更新", webview)
        self.assertIn("WebView2", host)
        self.assertIn("selectedGame", host)
        self.assertIn("launchConfirmed", host)
        self.assertIn("chrome.webview.postMessage", webview)
        self.assertIn('value="enabled"', webview)
        self.assertIn('value="balanced"', webview)
        self.assertNotIn('id="thinkingToggle" data-action="thinking"', webview)
        self.assertIn("border-radius:0", webview)
        self.assertNotIn("linear-gradient", webview)
        self.assertIn("../../assets/open-shift-icon.svg", webview)
        build = (root / "packaging" / "build-player-release.ps1").read_text(encoding="utf-8")
        self.assertIn('$WebViewSdk', build)
        self.assertIn('0.24.0-preview.2', build)
        self.assertNotIn('0.24.0-preview.1', build)
        self.assertIn('OPEN_SHIFT_WEBVIEW2_SDK', build)
        self.assertIn('Get-Command -Name $PythonPath -CommandType Application', build)
        self.assertIn('Select-Object -First 1', build)
        self.assertIn('Where-Object { $_.Source }', build)
        self.assertIn('Resolve-Path -LiteralPath $PythonPath', build)
        self.assertIn('$pythonExe = Resolve-PythonExecutable $Python', build)
        self.assertIn('$pyinstaller = Join-Path $pythonDir "Scripts\\pyinstaller.exe"', build)
        self.assertIn('& $pythonExe -m PyInstaller', build)
        low_level = (root / "packaging" / "install-isolated-copy.ps1").read_text(encoding="utf-8")
        self.assertNotIn('"verify-patch-output"', low_level)
        self.assertIn("$reuseVerifiedPatch", low_level)
        self.assertIn("already matches the newly verified patch output", low_level)
        self.assertNotIn("Get-FileHash", low_level)
        self.assertIn("WaitForSingleObject", host)
        self.assertIn("PACKAGE_MANIFEST.json", host)
        self.assertIn("WriteUtf8(path, report.str())", host)
        self.assertIn('OpenShift-" + SafeVersion', host)
        self.assertIn("const size_t steamRootCount = roots.size()", host)
        self.assertIn("index < steamRootCount", host)
        self.assertIn("std::find_if(roots.begin(), roots.end(), samePath)", host)

    def test_bundled_package_excludes_development_sources(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "OpenShift.exe"
            gui = Path(temp_dir) / "OpenShiftSetup.exe"
            delta = Path(temp_dir) / "data-win.delta"
            runtime.write_bytes(b"runtime")
            gui.write_bytes(b"gui")
            delta.write_bytes(b"delta")
            output = Path(temp_dir) / "release.zip"
            build_mod_package(project_root=root, output=output, runtime_exe=runtime, gui_exe=gui, data_delta=delta)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
                self.assertFalse(any(name.startswith("src/") for name in names))
                self.assertFalse(any(name.startswith("game-patch/gml/") for name in names))
                self.assertFalse(any(name.endswith("apply_mod.csx") for name in names))
                self.assertFalse(manifest["contains_development_sources"])

    def test_public_project_docs_keep_community_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("# OPEN SHIFT", readme)
        self.assertIn("欢迎提交", readme)
        icon = (root / "assets" / "open-shift-icon.svg").read_text(encoding="utf-8")
        self.assertIn("shape-rendering=\"crispEdges\"", icon)
        self.assertIn("OPEN SHIFT pixel bar icon", icon)
        self.assertIn("assets/screenshots/acceptance-day1-world-state.png", readme)
        self.assertIn("assets/screenshots/acceptance-day2-world-state.png", readme)
        self.assertIn("assets/screenshots/acceptance-dana-opening.png", readme)
        self.assertIn("assets/screenshots/acceptance-music-selection.png", readme)
        self.assertIn("assets/screenshots/acceptance-order-details.png", readme)
        self.assertIn("assets/screenshots/acceptance-break-save.png", readme)
        self.assertNotIn("assets/screenshots/bar-stella-order.png", readme)
        self.assertNotIn("assets/screenshots/jill-room-day-2.png", readme)
        self.assertNotIn("assets/screenshots/chinese-installer.png", readme)
        self.assertIn("不要提交什么", contributing)

    def test_optional_release_tools_use_fixed_non_original_names(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "OpenShift.exe"
            delta = Path(temp_dir) / "data-win.delta"
            runtime.write_bytes(b"runtime")
            delta.write_bytes(b"delta")
            output = Path(temp_dir) / "release.zip"
            build_mod_package(
                project_root=root,
                output=output,
                runtime_exe=runtime,
                data_delta=delta,
            )
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("OpenShift.exe", names)
                self.assertIn("patch/data-win.delta", names)
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

    def test_acceptance_launcher_rejects_stale_copies_and_auto_selects_latest_build(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "packaging" / "launch-deepseek-acceptance.ps1").read_text(encoding="utf-8")
        self.assertIn('[string] $GameCopyDir = ""', launcher)
        self.assertIn('[string] $ExpectedPatchedDataWinSha256 = ""', launcher)
        self.assertIn('Read-InstallRecords', launcher)
        self.assertIn('$latestRecord = $records | Select-Object -First 1', launcher)
        self.assertIn('install.json hash record', launcher)
        self.assertIn('Refusing to start a stale or unsupported game copy.', launcher)
        self.assertIn('Actual data.win SHA256:', launcher)
        self.assertIn('Expected current SHA256:', launcher)
        self.assertIn('Get-Sha256Hex', launcher)

    def test_stage23_docs_describe_lightweight_versioned_instances(self) -> None:
        root = Path(__file__).resolve().parents[1]
        install = (root / "packaging" / "INSTALL.md").read_text(encoding="utf-8")
        packaging = (root / "packaging" / "README.md").read_text(encoding="utf-8")
        roadmap = (root / "docs" / "roadmap.md").read_text(encoding="utf-8")
        for text in (install, packaging, roadmap):
            self.assertIn("OpenShift-<package_version>", text)
            self.assertIn("HardLink", text)
            self.assertIn("Junction", text)
            self.assertIn("SymbolicLink", text)
        self.assertIn("绝不递归复制完整游戏目录", install)
        self.assertIn("不会静默回退为完整复制", packaging)


if __name__ == "__main__":
    unittest.main()
