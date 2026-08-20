"""Build a safe, source-only Open Shift Mod distribution archive."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


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
    "README.md",
    "pyproject.toml",
    "game-patch/README.md",
    "game-patch/manifest.json",
    "game-patch/apply_mod.csx",
    "game-patch/analysis/verify_patch.csx",
    "packaging/README.md",
    "packaging/install-isolated-copy.ps1",
    "packaging/launch-open-shift.ps1",
    "packaging/launch-deepseek-acceptance.ps1",
    "packaging/open-shift.toml.example",
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


def build_mod_package(
    *, project_root: str | Path, output: str | Path, version: str = "0.1.0"
) -> PackageResult:
    """Create a source-only zip that can patch a user's own game copy.

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
    files = _relative_files(root)
    names = [_zip_name(path, root) for path in files]
    forbidden = _forbidden(names)
    if forbidden:
        raise PackageError("forbidden files would enter package: " + ", ".join(forbidden))

    manifest = {
        "package_id": "open_shift",
        "package_version": version,
        "source_only": True,
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
            archive.writestr(
                "PACKAGE_MANIFEST.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
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
