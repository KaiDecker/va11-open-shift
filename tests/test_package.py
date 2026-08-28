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
        gui = (root / "packaging" / "open-shift-gui.ps1").read_text(encoding="utf-8")
        host = (root / "packaging" / "gui_launcher.cs").read_text(encoding="utf-8")
        webview = (root / "packaging" / "webview" / "index.html").read_text(encoding="utf-8")
        self.assertIn("安装 / 修复", gui)
        self.assertIn("准备并启动", gui)
        self.assertIn("Steam 原版 data.win：只读输入", gui)
        self.assertIn("Save-ApiKey", gui)
        self.assertNotIn("$keyBox.Clear()", gui)
        self.assertIn('"-SkipShortcut"', gui)
        self.assertIn("launcher.log", gui)
        self.assertIn("libraryfolders.vdf", gui)
        self.assertIn("Complete-Log", gui)
        self.assertIn("$script:lastLog", gui)
        self.assertNotIn("Safe installer and launcher", gui)
        self.assertIn("$form.Icon", gui)
        self.assertIn("OPEN SHIFT - Launcher", gui)
        self.assertIn("Set-ButtonStyle", gui)
        self.assertIn("FromArgb(239, 241, 247)", gui)
        self.assertIn("FromArgb(248, 249, 252)", gui)
        self.assertIn("PLAYER 0.19 RC.32", gui)
        self.assertNotIn("PLAYER 0.19 RC.28", gui)
        self.assertIn("PLAYER 0.19 RC.32", webview)
        self.assertIn("准备并启动", gui)
        self.assertIn("brandIcon", gui)
        self.assertIn("WebView2", host)
        self.assertIn("CoreWebView2", host)
        self.assertIn("WebView2RuntimeNotFoundException", host)
        self.assertIn("libraryfolders.vdf", host)
        self.assertIn("selectedSteamGame", host)
        self.assertIn('action == "steamChanged"', host)
        self.assertIn('action == "thinking"', host)
        self.assertIn("ReadThinkingMode", host)
        self.assertIn("SetThinkingMode", host)
        self.assertIn("default|enabled|balanced|disabled", host)
        self.assertIn("ValidateRuntimeConfig", host)
        self.assertIn("validate-config --config", host)
        self.assertIn("File.Replace", host)
        self.assertIn("MessageBoxButtons.YesNo", host)
        self.assertIn("markerExists", host)
        self.assertIn("launchConfirmed", host)
        self.assertIn("chrome.webview.postMessage", webview)
        self.assertIn("send(a,{value:key.value});}", webview)
        self.assertNotIn("key.value=''", webview)
        self.assertIn("applyAvailability", webview)
        self.assertIn("button.disabled=busy", webview)
        self.assertIn("DeepSeek 生成模式", webview)
        self.assertIn("平衡", webview)
        self.assertIn("深度", webview)
        self.assertIn('role="switch"', webview)
        self.assertIn("thinkingToggle.disabled=busy||!thinkingAvailable", webview)
        self.assertIn('value="enabled"', webview)
        self.assertIn('value="balanced"', webview)
        self.assertIn("border-radius:24px", webview)
        self.assertIn("../../assets/open-shift-icon.svg", webview)
        self.assertNotIn("class=\"traffic\"", webview)
        self.assertNotIn('data-action="minimize"', webview)
        self.assertNotIn('data-action="close"', webview)
        self.assertNotIn("open-shift-gui.ps1", host)
        project = (root / "packaging" / "OpenShiftSetup.csproj").read_text(encoding="utf-8")
        self.assertIn("ApplicationIcon", project)
        build = (root / "packaging" / "build-player-release.ps1").read_text(encoding="utf-8")
        self.assertIn("PublishSingleFile=true", build)
        self.assertIn("--self-contained true", build)
        self.assertIn('0.19.0-rc.32', build)
        self.assertIn('$WebViewSdk', build)
        self.assertIn('OPEN_SHIFT_WEBVIEW2_SDK', build)
        self.assertIn('Get-Command -Name $PythonPath -CommandType Application', build)
        self.assertIn('Select-Object -First 1', build)
        self.assertIn('Where-Object { $_.Source }', build)
        self.assertIn('Resolve-Path -LiteralPath $PythonPath', build)
        self.assertIn('$pythonExe = Resolve-PythonExecutable $Python', build)
        self.assertIn('$pyinstaller = Join-Path $pythonDir "Scripts\\pyinstaller.exe"', build)
        self.assertIn('& $pythonExe -m PyInstaller', build)
        low_level = (root / "packaging" / "install-isolated-copy.ps1").read_text(encoding="utf-8")
        self.assertLess(low_level.index('"verify-patch-output"'), low_level.index("New-Item -ItemType Directory -Force -Path $destinationRoot"))
        self.assertIn("$reuseVerifiedPatch", low_level)
        self.assertIn("already matches the newly verified patch output", low_level)
        self.assertNotIn("Get-FileHash", low_level)
        self.assertIn("WaitForExit", gui)
        self.assertIn("Refresh()", gui)
        self.assertIn("$installCompleted", gui)
        self.assertIn("$script:launchConfirmed", gui)
        self.assertIn("Entering main loop", gui)
        self.assertIn("游戏已启动，可以关闭此窗口。", gui)
        self.assertIn("-SkipShortcut", host)
        self.assertIn("ReadPackageVersion", host)
        self.assertIn("NormalizePackageVersion", host)
        self.assertIn('"OpenShift-" + safeVersion', host)
        self.assertIn("ExistingInstallMatches", host)
        self.assertIn("PACKAGE_MANIFEST.json", host)
        self.assertIn("PACKAGE_MANIFEST.json", gui)
        self.assertIn('"OpenShift-" + $safeVersion', gui)

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
