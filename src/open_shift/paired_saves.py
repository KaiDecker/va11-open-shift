"""Atomic pairing between the original save slots and Agent world snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4


PAIRED_SAVE_FORMAT_VERSION = 1
ORIGINAL_SAVE_SLOT_COUNT = 24
_REVISION = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class PairedSaveError(RuntimeError):
    def __init__(self, code: str, layer: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.layer = layer


class PairedSaveMismatch(PairedSaveError):
    pass


class WorldSessionCheckpoint:
    """Roll the live SQLite world back to the latest in-session save point."""

    def __init__(self, live_db_path: str | Path, checkpoint_path: str | Path) -> None:
        self.live_db_path = Path(live_db_path).resolve()
        self.checkpoint_path = Path(checkpoint_path).resolve()
        self.state_path = self.checkpoint_path.with_suffix(
            f"{self.checkpoint_path.suffix}.json"
        )
        self._lock = threading.RLock()

    def _write_state(self, had_live_database: bool) -> None:
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{uuid4().hex}.tmp"
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_text(
                json.dumps(
                    {"format_version": 1, "had_live_database": had_live_database},
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_state(self) -> bool:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PairedSaveError(
                "invalid_session_checkpoint",
                "session_checkpoint",
                "the session checkpoint state was invalid",
            ) from exc
        if set(value) != {"format_version", "had_live_database"} or (
            value["format_version"] != 1
            or not isinstance(value["had_live_database"], bool)
        ):
            raise PairedSaveError(
                "invalid_session_checkpoint",
                "session_checkpoint",
                "the session checkpoint state was invalid",
            )
        return bool(value["had_live_database"])

    @staticmethod
    def _backup(source_path: Path, destination_path: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.with_name(
            f".{destination_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with closing(
                sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
            ) as source:
                with closing(sqlite3.connect(temporary)) as target:
                    source.backup(target)
                    _integrity_check(target)
            os.replace(temporary, destination_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _remove_database_files(path: Path) -> None:
        path.unlink(missing_ok=True)
        path.with_name(f"{path.name}-wal").unlink(missing_ok=True)
        path.with_name(f"{path.name}-shm").unlink(missing_ok=True)

    @staticmethod
    def _merge_safe_story_drafts(source_path: Path, target_path: Path) -> None:
        """Keep unplayed graphs whose source state still exists after rollback."""

        if not source_path.is_file() or not target_path.is_file():
            return
        with closing(
            sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        ) as source, closing(sqlite3.connect(target_path)) as target:
            source.row_factory = sqlite3.Row
            target_tick_row = target.execute(
                "SELECT value FROM world_meta WHERE key = 'current_tick'"
            ).fetchone()
            target_tick = int(target_tick_row[0]) if target_tick_row else 0
            target_event_ids = {
                int(row[0]) for row in target.execute("SELECT event_id FROM events")
            }
            rows = source.execute(
                "SELECT * FROM daily_story_graphs WHERE status = 'ready'"
            ).fetchall()
            for row in rows:
                source_ids = tuple(json.loads(row["source_event_ids_json"]))
                if (
                    int(row["source_tick"]) > target_tick
                    or not set(source_ids).issubset(target_event_ids)
                ):
                    continue
                graph = json.loads(row["graph_json"])
                target.execute(
                    """
                    INSERT INTO daily_story_graphs(
                        day_index, generation_version, status, source_tick,
                        source_event_ids_json, graph_json, error_code, attempt_count
                    ) VALUES(?, ?, 'ready', ?, ?, ?, NULL, ?)
                    ON CONFLICT(day_index, generation_version) DO NOTHING
                    """,
                    (
                        row["day_index"],
                        row["generation_version"],
                        row["source_tick"],
                        row["source_event_ids_json"],
                        row["graph_json"],
                        row["attempt_count"],
                    ),
                )
                target.execute(
                    """
                    INSERT INTO daily_story_progress(
                        day_index, generation_version, status, current_node_id,
                        committed_branch_count
                    ) VALUES(?, ?, 'active', ?, 0)
                    ON CONFLICT(day_index, generation_version) DO NOTHING
                    """,
                    (
                        row["day_index"],
                        row["generation_version"],
                        graph["entry_node_id"],
                    ),
                )
            target.commit()
            _integrity_check(target)

    def begin(self) -> None:
        """Capture the state that should survive if the player does not save."""

        with self._lock:
            self.checkpoint_path.unlink(missing_ok=True)
            if self.live_db_path.is_file():
                self._backup(self.live_db_path, self.checkpoint_path)
                self._write_state(True)
            else:
                self._write_state(False)

    def recover_abandoned_session(self) -> bool:
        """Restore a checkpoint left by a launcher or game process crash."""

        with self._lock:
            if not self.state_path.is_file():
                self.checkpoint_path.unlink(missing_ok=True)
                return False
            self.rollback()
            self.cleanup()
            return True

    def capture(self) -> None:
        """Advance the session recovery point after a paired save or restore."""

        with self._lock:
            if not self.live_db_path.is_file():
                raise PairedSaveError(
                    "world_database_missing",
                    "session_checkpoint",
                    "the live world database was missing",
                )
            self._backup(self.live_db_path, self.checkpoint_path)
            self._write_state(True)

    def rollback(self) -> None:
        """Discard progress made after the latest captured recovery point."""

        with self._lock:
            if not self.state_path.is_file():
                return
            had_live_database = self._read_state()
            if had_live_database:
                if not self.checkpoint_path.is_file():
                    raise PairedSaveError(
                        "session_checkpoint_missing",
                        "session_checkpoint",
                        "the session recovery database was missing",
                    )
                temporary = self.live_db_path.with_name(
                    f".{self.live_db_path.name}.{uuid4().hex}.session.tmp"
                )
                try:
                    self._backup(self.checkpoint_path, temporary)
                    self._merge_safe_story_drafts(self.live_db_path, temporary)
                    self._remove_database_files(self.live_db_path)
                    os.replace(temporary, self.live_db_path)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                self._remove_database_files(self.live_db_path)

    def cleanup(self) -> None:
        with self._lock:
            self.checkpoint_path.unlink(missing_ok=True)
            self.state_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class PairedSaveRecord:
    slot: int
    revision: str
    created_at_utc: str
    native_save_sha256: str
    native_save_size: int
    native_save_mtime_ns: int
    snapshot_sha256: str
    schema_version: int
    world_tick: int
    world_day: int
    opening_seen: bool
    player_shift_income: int
    shift_phase: str
    last_completed_story_day: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": PAIRED_SAVE_FORMAT_VERSION,
            "slot": self.slot,
            "revision": self.revision,
            "created_at_utc": self.created_at_utc,
            "native_save": {
                "sha256": self.native_save_sha256,
                "size": self.native_save_size,
                "mtime_ns": self.native_save_mtime_ns,
            },
            "world": {
                "snapshot_sha256": self.snapshot_sha256,
                "schema_version": self.schema_version,
                "world_tick": self.world_tick,
                "world_day": self.world_day,
                "opening_seen": self.opening_seen,
                "player_shift_income": self.player_shift_income,
                "shift_phase": self.shift_phase,
                "last_completed_story_day": self.last_completed_story_day,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PairedSaveRecord":
        if set(value) != {
            "format_version",
            "slot",
            "revision",
            "created_at_utc",
            "native_save",
            "world",
        }:
            raise ValueError("paired save record fields did not match the schema")
        if value["format_version"] != PAIRED_SAVE_FORMAT_VERSION:
            raise ValueError("paired save format version was unsupported")
        native = value["native_save"]
        world = value["world"]
        if not isinstance(native, Mapping) or set(native) != {
            "sha256",
            "size",
            "mtime_ns",
        }:
            raise ValueError("paired native save fields were invalid")
        if not isinstance(world, Mapping) or set(world) != {
            "snapshot_sha256",
            "schema_version",
            "world_tick",
            "world_day",
            "opening_seen",
            "player_shift_income",
            "shift_phase",
            "last_completed_story_day",
        }:
            raise ValueError("paired world snapshot fields were invalid")
        slot = value["slot"]
        revision = value["revision"]
        created_at_utc = value["created_at_utc"]
        native_hash = native["sha256"]
        snapshot_hash = world["snapshot_sha256"]
        integers = (
            native["size"],
            native["mtime_ns"],
            world["schema_version"],
            world["world_tick"],
            world["world_day"],
            world["player_shift_income"],
            world["last_completed_story_day"],
        )
        if (
            isinstance(slot, bool)
            or not isinstance(slot, int)
            or not 1 <= slot <= ORIGINAL_SAVE_SLOT_COUNT
            or not isinstance(revision, str)
            or not _REVISION.fullmatch(revision)
            or not isinstance(created_at_utc, str)
            or not created_at_utc.endswith("Z")
            or not isinstance(native_hash, str)
            or not _SHA256.fullmatch(native_hash)
            or not isinstance(snapshot_hash, str)
            or not _SHA256.fullmatch(snapshot_hash)
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in integers)
            or not isinstance(world["opening_seen"], bool)
            or not isinstance(world["shift_phase"], str)
            or world["shift_phase"] not in {"playing", "save_required"}
        ):
            raise ValueError("paired save record values were invalid")
        return cls(
            slot,
            revision,
            created_at_utc,
            native_hash,
            int(native["size"]),
            int(native["mtime_ns"]),
            snapshot_hash,
            int(world["schema_version"]),
            int(world["world_tick"]),
            int(world["world_day"]),
            bool(world["opening_seen"]),
            int(world["player_shift_income"]),
            str(world["shift_phase"]),
            int(world["last_completed_story_day"]),
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_check(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    if row is None or row[0] != "ok":
        raise sqlite3.DatabaseError("SQLite integrity check failed")


class PairedSaveManager:
    """Keep immutable revisions and atomically advance one slot pointer."""

    def __init__(
        self,
        live_db_path: str | Path,
        native_save_dir: str | Path,
        snapshot_root: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.live_db_path = Path(live_db_path).resolve()
        self.native_save_dir = Path(native_save_dir).resolve()
        self.snapshot_root = Path(snapshot_root).resolve()
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()

    @staticmethod
    def _validate_slot(slot: int) -> int:
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= ORIGINAL_SAVE_SLOT_COUNT:
            raise PairedSaveError("invalid_slot", "contract", "save slot must be between 1 and 24")
        return slot

    def native_save_path(self, slot: int) -> Path:
        slot = self._validate_slot(slot)
        return self.native_save_dir / f"Record of Waifu Wars[{slot}].txt"

    def _slot_dir(self, slot: int) -> Path:
        return self.snapshot_root / f"slot-{self._validate_slot(slot):02d}"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PairedSaveError("invalid_metadata", "metadata", "paired save metadata could not be read") from exc
        if not isinstance(value, dict):
            raise PairedSaveError("invalid_metadata", "metadata", "paired save metadata was not an object")
        return value

    @staticmethod
    def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_copy_file(source: Path, destination: Path) -> None:
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
                for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_current_pointer(self, slot_dir: Path, revision: str) -> None:
        self._atomic_write_json(
            slot_dir / "current.json",
            {"format_version": PAIRED_SAVE_FORMAT_VERSION, "revision": revision},
        )

    @staticmethod
    def _snapshot_world_metadata(path: Path) -> dict[str, int | bool | str]:
        with closing(sqlite3.connect(path)) as connection:
            _integrity_check(connection)
            rows = dict(connection.execute("SELECT key, value FROM world_meta"))
        try:
            schema_version = int(rows["schema_version"])
            world_tick = int(rows.get("current_tick", "0"))
            world_day = int(rows.get("current_story_day", "1"))
            player_shift_income = int(rows.get("player_shift_income", "0"))
            last_completed_story_day = int(rows.get("last_completed_story_day", "0"))
        except (KeyError, TypeError, ValueError) as exc:
            raise sqlite3.DatabaseError("world snapshot metadata was invalid") from exc
        return {
            "schema_version": schema_version,
            "world_tick": world_tick,
            "world_day": world_day,
            "opening_seen": rows.get(f"bridge_ack:opening_day_{world_day}") is not None,
            "player_shift_income": player_shift_income,
            "shift_phase": rows.get("shift_phase", "playing"),
            "last_completed_story_day": last_completed_story_day,
        }

    @staticmethod
    def _stamp_snapshot(path: Path, slot: int, revision: str, native_hash: str) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            values = {
                "paired_save_revision": revision,
                "paired_save_slot": str(slot),
                "paired_native_save_sha256": native_hash,
            }
            if connection.execute(
                "SELECT value FROM world_meta WHERE key = 'shift_phase'"
            ).fetchone() == ("save_required",):
                values["shift_phase"] = "playing"
            connection.executemany(
                "INSERT INTO world_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                values.items(),
            )
            connection.commit()
            _integrity_check(connection)

    @staticmethod
    def _request_fingerprint(request: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            dict(request), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _operation_path(self, operation: str, operation_id: str) -> Path:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self.snapshot_root / "operations" / f"{operation}-{digest}.json"

    def _replay_operation(
        self,
        operation: str,
        operation_id: str | None,
        request: Mapping[str, Any] | None,
    ) -> PairedSaveRecord | None:
        if operation_id is None and request is None:
            return None
        if (
            operation_id is None
            or request is None
            or not _OPERATION_ID.fullmatch(operation_id)
        ):
            raise PairedSaveError(
                "invalid_operation", "contract", "paired save operation identity was invalid"
            )
        path = self._operation_path(operation, operation_id)
        if not path.exists():
            return None
        value = self._read_json(path)
        expected = {"format_version", "operation", "operation_id", "request_sha256", "record"}
        if set(value) != expected or (
            value["format_version"] != PAIRED_SAVE_FORMAT_VERSION
            or value["operation"] != operation
            or value["operation_id"] != operation_id
        ):
            raise PairedSaveError(
                "invalid_operation_receipt", "metadata", "paired save operation receipt was invalid"
            )
        if value["request_sha256"] != self._request_fingerprint(request):
            raise PairedSaveError(
                "operation_id_conflict", "contract", "paired save operation id was reused"
            )
        try:
            return PairedSaveRecord.from_dict(value["record"])
        except (TypeError, ValueError) as exc:
            raise PairedSaveError(
                "invalid_operation_receipt", "metadata", "paired save operation record was invalid"
            ) from exc

    def _record_operation(
        self,
        operation: str,
        operation_id: str | None,
        request: Mapping[str, Any] | None,
        record: PairedSaveRecord,
    ) -> None:
        if operation_id is None or request is None:
            return
        path = self._operation_path(operation, operation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(
            path,
            {
                "format_version": PAIRED_SAVE_FORMAT_VERSION,
                "operation": operation,
                "operation_id": operation_id,
                "request_sha256": self._request_fingerprint(request),
                "record": record.to_dict(),
            },
        )

    def save_slot(
        self,
        slot: int,
        *,
        operation_id: str | None = None,
        request: Mapping[str, Any] | None = None,
    ) -> PairedSaveRecord:
        with self._lock:
            prior = self._replay_operation("pair", operation_id, request)
            if prior is not None:
                return prior
            record = self._save_slot(slot)
            self._record_operation("pair", operation_id, request, record)
            return record

    def _save_slot(self, slot: int) -> PairedSaveRecord:
        slot = self._validate_slot(slot)
        prior_record = self._current_record(slot)
        native_path = self.native_save_path(slot)
        if not native_path.is_file():
            raise PairedSaveError("native_save_missing", "native_save", "the original save file was missing")
        if not self.live_db_path.is_file():
            raise PairedSaveError("world_database_missing", "snapshot", "the live world database was missing")
        native_stat = native_path.stat()
        native_hash = _sha256_file(native_path)
        revision = uuid4().hex
        slot_dir = self._slot_dir(slot)
        slot_dir.mkdir(parents=True, exist_ok=True)
        temporary_db = slot_dir / f".{revision}.sqlite3.tmp"
        final_db = slot_dir / f"{revision}.sqlite3"
        final_metadata = slot_dir / f"{revision}.json"
        final_native = slot_dir / f"{revision}.native.txt"
        try:
            with closing(
                sqlite3.connect(f"file:{self.live_db_path}?mode=ro", uri=True)
            ) as source:
                with closing(sqlite3.connect(temporary_db)) as target:
                    source.backup(target)
            self._stamp_snapshot(temporary_db, slot, revision, native_hash)
            world = self._snapshot_world_metadata(temporary_db)
            snapshot_hash = _sha256_file(temporary_db)
            record = PairedSaveRecord(
                slot=slot,
                revision=revision,
                created_at_utc=self._now().astimezone(UTC).isoformat().replace("+00:00", "Z"),
                native_save_sha256=native_hash,
                native_save_size=native_stat.st_size,
                native_save_mtime_ns=native_stat.st_mtime_ns,
                snapshot_sha256=snapshot_hash,
                schema_version=int(world["schema_version"]),
                world_tick=int(world["world_tick"]),
                world_day=int(world["world_day"]),
                opening_seen=bool(world["opening_seen"]),
                player_shift_income=int(world["player_shift_income"]),
                shift_phase=str(world["shift_phase"]),
                last_completed_story_day=int(world["last_completed_story_day"]),
            )
            os.replace(temporary_db, final_db)
            self._atomic_copy_file(native_path, final_native)
            self._atomic_write_json(final_metadata, record.to_dict())
            self._write_current_pointer(slot_dir, revision)
            return record
        except PairedSaveError:
            raise
        except Exception as exc:
            if prior_record is not None:
                prior_native = slot_dir / f"{prior_record.revision}.native.txt"
                try:
                    if (
                        not prior_native.is_file()
                        or _sha256_file(prior_native)
                        != prior_record.native_save_sha256
                    ):
                        raise OSError("previous paired native save was unavailable")
                    self._atomic_copy_file(prior_native, native_path)
                except Exception as rollback_error:
                    raise PairedSaveError(
                        "snapshot_and_native_rollback_failed",
                        "native_save",
                        "the snapshot failed and the previous original save could not be restored",
                    ) from rollback_error
            raise PairedSaveError("snapshot_failed", "snapshot", "the Agent world snapshot failed") from exc
        finally:
            temporary_db.unlink(missing_ok=True)

    def current_record(self, slot: int) -> PairedSaveRecord | None:
        with self._lock:
            return self._current_record(slot)

    def _current_record(self, slot: int) -> PairedSaveRecord | None:
        slot_dir = self._slot_dir(slot)
        pointer_path = slot_dir / "current.json"
        if not pointer_path.exists():
            return None
        pointer = self._read_json(pointer_path)
        if set(pointer) != {"format_version", "revision"} or pointer["format_version"] != PAIRED_SAVE_FORMAT_VERSION:
            raise PairedSaveError("invalid_pointer", "metadata", "paired save pointer was invalid")
        revision = pointer["revision"]
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise PairedSaveError("invalid_pointer", "metadata", "paired save revision was invalid")
        try:
            record = PairedSaveRecord.from_dict(self._read_json(slot_dir / f"{revision}.json"))
        except ValueError as exc:
            raise PairedSaveError("invalid_metadata", "metadata", str(exc)) from exc
        if record.slot != slot or record.revision != revision:
            raise PairedSaveError("invalid_metadata", "metadata", "paired save slot metadata did not match")
        return record

    def _verify_pair(self, slot: int, record: PairedSaveRecord) -> Path:
        native_path = self.native_save_path(slot)
        if not native_path.is_file():
            raise PairedSaveMismatch("native_save_missing", "native_save", "the paired original save file was missing")
        native_stat = native_path.stat()
        if native_stat.st_size != record.native_save_size or _sha256_file(native_path) != record.native_save_sha256:
            raise PairedSaveMismatch("native_save_mismatch", "native_save", "the original save did not match its Agent snapshot")
        snapshot_path = self._slot_dir(slot) / f"{record.revision}.sqlite3"
        if not snapshot_path.is_file() or _sha256_file(snapshot_path) != record.snapshot_sha256:
            raise PairedSaveMismatch("snapshot_mismatch", "snapshot", "the Agent snapshot hash did not match")
        native_copy = self._slot_dir(slot) / f"{record.revision}.native.txt"
        if not native_copy.is_file() or _sha256_file(native_copy) != record.native_save_sha256:
            raise PairedSaveMismatch(
                "native_snapshot_mismatch",
                "snapshot",
                "the immutable original save copy did not match",
            )
        with closing(
            sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
        ) as connection:
            _integrity_check(connection)
            rows = dict(
                connection.execute(
                    "SELECT key, value FROM world_meta WHERE key IN (?, ?, ?)",
                    ("paired_save_revision", "paired_save_slot", "paired_native_save_sha256"),
                )
            )
        if rows != {
            "paired_save_revision": record.revision,
            "paired_save_slot": str(slot),
            "paired_native_save_sha256": record.native_save_sha256,
        }:
            raise PairedSaveMismatch("snapshot_metadata_mismatch", "snapshot", "the Agent snapshot identity did not match")
        return snapshot_path

    def restore_slot(
        self,
        slot: int,
        *,
        operation_id: str | None = None,
        request: Mapping[str, Any] | None = None,
    ) -> PairedSaveRecord:
        with self._lock:
            prior = self._replay_operation("restore", operation_id, request)
            if prior is not None:
                return prior
            record = self._restore_slot(slot)
            self._record_operation("restore", operation_id, request, record)
            return record

    @staticmethod
    def _backup_database(source_path: Path, target_path: Path) -> None:
        with closing(
            sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(target_path)) as target:
                source.backup(target)
                _integrity_check(target)

    def _restore_live_from(self, snapshot_path: Path) -> None:
        self._backup_database(snapshot_path, self.live_db_path)

    def _restore_slot(self, slot: int) -> PairedSaveRecord:
        slot = self._validate_slot(slot)
        record = self._current_record(slot)
        if record is None:
            raise PairedSaveError("paired_save_missing", "metadata", "the save slot had no Agent snapshot")
        snapshot_path = self._verify_pair(slot, record)
        recovery = self.snapshot_root / f".restore-recovery-{uuid4().hex}.sqlite3"
        try:
            if self.live_db_path.is_file():
                self._backup_database(self.live_db_path, recovery)
            self._restore_live_from(snapshot_path)
            return record
        except Exception as exc:
            if recovery.is_file():
                try:
                    self._backup_database(recovery, self.live_db_path)
                except Exception:
                    pass
            raise PairedSaveError("restore_failed", "restore", "the Agent world restore failed") from exc
        finally:
            recovery.unlink(missing_ok=True)
