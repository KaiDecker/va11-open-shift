"""Build a safe Open Shift Mod distribution archive."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Collection


class PackageError(ValueError):
    """Raised when a release package cannot be built safely."""


@dataclass(frozen=True, slots=True)
class PackageResult:
    output: str
    file_count: int
    sha256: str
    forbidden_entries: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "built",
            "output": self.output,
            "file_count": self.file_count,
            "sha256": self.sha256,
            "forbidden_entries": list(self.forbidden_entries),
        }


_REQUIRED_FILES = (
    "assets/open-shift-icon.svg",
    "pyproject.toml",
    "game-patch/manifest.json",
    "game-patch/apply_mod.csx",
    "game-patch/analysis/verify_patch.csx",
    "packaging/install-isolated-copy.ps1",
    "packaging/install-open-shift.ps1",
    "packaging/configure-api-key.ps1",
    "packaging/launch-open-shift.ps1",
    "packaging/uninstall-open-shift.ps1",
    "packaging/open-shift.toml.example",
    "packaging/webview/index.html",
)
_FORBIDDEN_PARTS = (
    "reference-local/",
    "data.win",
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".ini",
    ".env",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_files(root: Path) -> tuple[Path, ...]:
    files: set[Path] = set()
    for relative in _REQUIRED_FILES:
        path = root / relative
        if not path.is_file():
            raise PackageError(f"required package file was missing: {relative}")
        files.add(path)
    gml_dir = root / "game-patch" / "gml"
    files.update(path for path in gml_dir.glob("*.gml") if path.is_file())
    files.update(path for path in (root / "src" / "open_shift").glob("*.py") if path.is_file())
    return tuple(sorted(files))


def _optional_file(path: str | Path | None, *, name: str, files: set[Path]) -> None:
    if path is None:
        return
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise PackageError(f"optional {name} was not a file: {candidate}")
    files.add(candidate)


def _zip_name(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _forbidden(entries: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            entry
            for entry in entries
            if any(part in entry.lower() for part in _FORBIDDEN_PARTS)
        )
    )


def _validate_archive(path: Path, expected_names: Collection[str]) -> None:
    """Re-open a newly written archive and verify every member's CRC."""
    expected = set(expected_names) | {"PACKAGE_MANIFEST.json"}
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise PackageError(f"package contained duplicate entries: {path}")
            actual = set(names)
            missing = sorted(expected - actual)
            if missing:
                raise PackageError(
                    "package validation found missing entries: " + ", ".join(missing)
                )
            bad_name = archive.testzip()
            if bad_name is not None:
                raise PackageError(f"package CRC validation failed for {bad_name}: {path}")
    except PackageError:
        raise
    except Exception as exc:
        # This also catches a truncated central directory and decompression
        # errors that PowerShell Expand-Archive would report less clearly.
        raise PackageError(f"package archive validation failed for {path}: {exc}") from exc


def build_mod_package(
    *,
    project_root: str | Path,
    output: str | Path,
    version: str = "0.1.0",
    runtime_exe: str | Path | None = None,
    gui_exe: str | Path | None = None,
    icon: str | Path | None = None,
    utmt_cli: str | Path | None = None,
    webview_dlls: tuple[str | Path, ...] = (),
) -> PackageResult:
    """Create a source-only or bundled-runtime zip for a user's own game copy.

    The archive deliberately contains no original executable, data.win,
    reference-local files, database, runtime INI, or API credentials.
    """

    root = Path(project_root).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if not root.is_dir():
        raise PackageError("project root was not a directory")
    if output_path == root or root in output_path.parents:
        relative_output = output_path.relative_to(root)
        if not relative_output.parts or relative_output.parts[0] not in {"dist", "work"}:
            raise PackageError("package output must not be inside the project source tree")
    files = tuple(sorted(_relative_files(root)))
    extras: list[tuple[Path, str]] = []
    if runtime_exe is not None:
        runtime_path = Path(runtime_exe).expanduser().resolve()
        _optional_file(runtime_path, name="runtime executable", files=set())
        extras.append((runtime_path, "OpenShift.exe"))
    if gui_exe is not None:
        gui_path = Path(gui_exe).expanduser().resolve()
        _optional_file(gui_path, name="GUI executable", files=set())
        extras.append((gui_path, "OpenShiftSetup.exe"))
    if icon is not None:
        icon_path = Path(icon).expanduser().resolve()
        _optional_file(icon_path, name="application icon", files=set())
        extras.append((icon_path, "OpenShift.ico"))
    if utmt_cli is not None:
        utmt_path = Path(utmt_cli).expanduser().resolve()
        _optional_file(utmt_path, name="UTMT CLI", files=set())
        extras.append((utmt_path, "tools/utmt/UndertaleModCli.zip"))
    for webview in webview_dlls:
        webview_path = Path(webview).expanduser().resolve()
        _optional_file(webview_path, name="WebView2 runtime library", files=set())
        extras.append((webview_path, webview_path.name))
    names = [_zip_name(path, root) for path in files] + [name for _, name in extras]
    forbidden = _forbidden(names)
    if forbidden:
        raise PackageError("forbidden files would enter package: " + ", ".join(forbidden))
    if len(names) != len(set(names)):
        raise PackageError("package contains duplicate output entries")

    manifest = {
        "package_id": "open_shift",
        "package_version": version,
        "source_only": runtime_exe is None,
        "contains_runtime_exe": runtime_exe is not None,
        "contains_gui_exe": gui_exe is not None,
        "contains_icon": icon is not None,
        "contains_utmt_cli": utmt_cli is not None,
        "contains_webview2": bool(webview_dlls),
        "requires_python": runtime_exe is None,
        "requires_user_owned_game_copy": True,
        "contains_original_data_win": False,
        "contains_api_key": False,
        "files": names,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, name in zip(files, names):
                archive.write(path, name)
            for path, name in extras:
                archive.write(path, name)
            archive.writestr(
                "PACKAGE_MANIFEST.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
        # Validate the closed temporary archive before replacing an existing
        # release. A build must never report success for a truncated ZIP.
        _validate_archive(Path(temporary), names)
        os.replace(temporary, output_path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return PackageResult(
        output=str(output_path),
        file_count=len(names) + 1,
        sha256=_sha256(output_path),
        forbidden_entries=(),
    )
