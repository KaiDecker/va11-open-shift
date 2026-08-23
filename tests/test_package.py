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
                self.assertIn("packaging/OpenShiftSetup.csproj", names)
                self.assertIn("packaging/webview/index.html", names)
                self.assertIn("packaging/configure-api-key.ps1", names)
                self.assertIn("packaging/build-player-release.ps1", names)
                self.assertIn("packaging/launch-open-shift.ps1", names)
                self.assertIn("packaging/launch-deepseek-acceptance.ps1", names)
                self.assertIn("packaging/uninstall-open-shift.ps1", names)
                self.assertIn("packaging/INSTALL.md", names)
                self.assertIn("CONTRIBUTING.md", names)
                self.assertIn("assets/open-shift-icon.svg", names)
                self.assertIn("assets/screenshots/bar-stella-order.png", names)
                self.assertIn("assets/screenshots/jill-room-day-2.png", names)
                self.assertIn("assets/screenshots/chinese-installer.png", names)
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
        self.assertIn("shortcut_path", installer)
        self.assertIn("$ownsShortcut", uninstaller)
        self.assertIn("was not owned by this installation", uninstaller)
        self.assertIn("Steam installation was not modified", uninstaller)
        self.assertIn("System.Security.Cryptography.ProtectedData", installer)
        self.assertIn("api-key.dpapi", installer)
        self.assertIn("OPEN_SHIFT_TIMING_LOG", installer)
        self.assertIn("timing.log", installer)
        self.assertIn("prefetch_days = 0", installer)
        self.assertIn("Expand-Archive", installer)
        self.assertIn("UndertaleModCli.zip", installer)
        self.assertIn("OpenShiftSetup.exe", installer)
        self.assertIn("WebView2Loader.dll", installer)
        self.assertIn("OpenShift.ico", installer)
        self.assertIn('"OpenShift-" + $patchFingerprint.Substring(0, 12) + ".ico"', installer)
        self.assertIn("Remove-Item -LiteralPath $shortcut", installer)
        self.assertIn("$link.IconLocation = \"$installedIcon,0\"", installer)
        self.assertIn("ie4uinit.exe", installer)
        self.assertIn("$CompletionMarker", installer)
        self.assertIn("patch_fingerprint", installer)
        self.assertIn("package_version", installer)
        self.assertIn("libraryfolders.vdf", installer)
        self.assertIn("Get-PatchFingerprint", installer)
        self.assertIn("Get-Sha256Hex", installer)
        self.assertNotIn("Get-FileHash", installer)
        self.assertIn('Join-Path $packageRoot "assets"', installer)
        self.assertIn("exit 0", installer)
        self.assertIn('Join-Path $packageRoot "tools"', installer)
        self.assertIn("$sameRoot", installer)
        self.assertLess(installer.index("Write-Utf8NoBom $launcher"), installer.index("if (-not $SkipShortcut)"))
        self.assertIn("$link.TargetPath = $installedGui", installer)
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
        self.assertIn("PLAYER 0.17 RC", webview)
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
        self.assertIn('0.17.0-rc.1', build)
        self.assertIn('$WebViewSdk', build)
        self.assertIn('OPEN_SHIFT_WEBVIEW2_SDK', build)
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

    def test_public_project_docs_keep_community_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("# OPEN SHIFT", readme)
        self.assertIn("欢迎参与", readme)
        icon = (root / "assets" / "open-shift-icon.svg").read_text(encoding="utf-8")
        self.assertIn("shape-rendering=\"crispEdges\"", icon)
        self.assertIn("OPEN SHIFT pixel bar icon", icon)
        self.assertIn("assets/screenshots/bar-stella-order.png", readme)
        self.assertIn("assets/screenshots/jill-room-day-2.png", readme)
        self.assertIn("assets/screenshots/chinese-installer.png", readme)
        self.assertIn("不要提交什么", contributing)

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
