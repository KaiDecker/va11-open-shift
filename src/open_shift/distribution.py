"""Safe install and uninstall operations for an isolated GameMaker copy."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .game_data import GameDataError, GameDataInventory, inspect_game_data
from .patch_contract import (
    PatchContractError,
    PatchManifest,
    validate_patch_source_tree,
    validate_patch_target,
)


class DistributionError(ValueError):
    """Raised when an install or uninstall safety condition fails."""


@dataclass(frozen=True, slots=True)
class InstallRecord:
    schema_version: int
    mod_id: str
    installed_data_win: str
    original_sha256: str
    installed_sha256: str
    backup_path: str
    installed_at_utc: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except FileNotFoundError:
        return left == right


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _backup_name(destination: Path, digest: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{destination.stem}.{timestamp}.{digest[:12]}.data.win"  # noqa: B023


def _validate_patched_inventory(
    manifest: PatchManifest,
    original: GameDataInventory,
    patched: GameDataInventory,
) -> None:
    validate_patch_target(manifest, original)
    if patched.sha256 == original.sha256:
        raise DistributionError("patched data.win was identical to the original baseline")
    names = set(patched.resource_names)
    missing = sorted(set(manifest.new_resources) - names)
    if missing:
        raise DistributionError(
            f"patched data.win was missing new resources: {', '.join(missing)}"
        )
    required_missing = sorted(set(manifest.required_resources) - names)
    if required_missing:
        raise DistributionError(
            f"patched data.win was missing original resources: {', '.join(required_missing)}"
        )


def verify_patch_output(
    *,
    original_data_win: str | Path,
    patched_data_win: str | Path,
    manifest: PatchManifest,
    gml_source_dir: str | Path,
) -> dict[str, object]:
    """Verify a release candidate without writing either game file."""

    original_path = _resolved(original_data_win)
    patched_path = _resolved(patched_data_win)
    try:
        original = inspect_game_data(original_path)
        patched = inspect_game_data(patched_path)
        _validate_patched_inventory(manifest, original, patched)
        sources = validate_patch_source_tree(gml_source_dir)
    except (OSError, GameDataError, PatchContractError) as exc:
        raise DistributionError(f"release candidate verification failed: {exc}") from exc
    return {
        "status": "verified",
        "original_sha256": original.sha256,
        "patched_sha256": patched.sha256,
        "patched_size": patched.file_size,
        "gml_source_count": len(sources),
        "new_resources": list(manifest.new_resources),
    }


def install_patch(
    *,
    original_data_win: str | Path,
    patched_data_win: str | Path,
    destination_data_win: str | Path,
    backup_dir: str | Path,
    manifest: PatchManifest,
    record_path: str | Path | None = None,
) -> InstallRecord:
    """Install a verified patch into an isolated destination with a backup."""

    original_path = _resolved(original_data_win)
    patched_path = _resolved(patched_data_win)
    destination_path = _resolved(destination_data_win)
    backup_root = _resolved(backup_dir)
    if not original_path.is_file() or not patched_path.is_file():
        raise DistributionError("original and patched data.win files must exist")
    if _same_path(original_path, destination_path):
        raise DistributionError("refusing to install over the verified original data.win")
    if _same_path(patched_path, destination_path):
        raise DistributionError("patched source and destination data.win must differ")
    try:
        original_inventory = inspect_game_data(original_path)
        patched_inventory = inspect_game_data(patched_path)
    except (OSError, GameDataError) as exc:
        raise DistributionError(f"could not inspect data.win: {exc}") from exc
    try:
        _validate_patched_inventory(manifest, original_inventory, patched_inventory)
    except PatchContractError as exc:
        raise DistributionError(str(exc)) from exc

    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / _backup_name(destination_path, patched_inventory.sha256)
    backup_path: Path | None = None
    if destination_path.exists():
        if not destination_path.is_file():
            raise DistributionError("destination data.win was not a regular file")
        destination_inventory = inspect_game_data(destination_path)
        if destination_inventory.sha256 == patched_inventory.sha256:
            raise DistributionError("destination already contains this patch output")
        backup_path = backup_root / _backup_name(destination_path, destination_inventory.sha256)
        _atomic_copy(destination_path, backup_path)

    _atomic_copy(patched_path, destination_path)
    record = InstallRecord(
        schema_version=1,
        mod_id=manifest.mod_id,
        installed_data_win=str(destination_path),
        original_sha256=original_inventory.sha256,
        installed_sha256=patched_inventory.sha256,
        backup_path=str(backup_path) if backup_path is not None else "",
        installed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    if record_path is not None:
        record_file = _resolved(record_path)
        record_file.parent.mkdir(parents=True, exist_ok=True)
        record_file.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return record


def uninstall_patch(*, record_path: str | Path) -> InstallRecord:
    """Restore the recorded backup only when the installed output is unchanged."""

    record_file = _resolved(record_path)
    try:
        value = json.loads(record_file.read_text(encoding="utf-8"))
        record = InstallRecord(**value)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DistributionError(f"install record was invalid: {exc}") from exc
    if record.schema_version != 1 or not record.mod_id:
        raise DistributionError("install record schema was unsupported")
    installed_path = _resolved(record.installed_data_win)
    if not installed_path.is_file():
        raise DistributionError("installed data.win was missing")
    current = inspect_game_data(installed_path)
    if current.sha256 != record.installed_sha256:
        raise DistributionError(
            "installed data.win changed after installation; refusing to restore blindly"
        )
    if not record.backup_path:
        raise DistributionError("no backup was recorded for this installation")
    backup_path = _resolved(record.backup_path)
    if not backup_path.is_file():
        raise DistributionError("recorded backup was missing")
    _atomic_copy(backup_path, installed_path)
    record_file.unlink()
    return record
