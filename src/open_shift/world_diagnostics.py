"""Read-only diagnostics and replay reports for a saved Open Shift world.

The bridge already records enough information to explain a run: authoritative
events and memories live in SQLite, while timing and rendered transcripts are
also optionally written as JSONL files.  This module deliberately opens the
database in SQLite's read-only mode and only returns a bounded, secret-redacted
view of that information.  It is therefore safe to run while investigating a
copy of a player's save without changing the save or retrying an LLM request.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


REPORT_VERSION = 1
_DAY_RE = re.compile(r"(?:^|[:_])day[_-]?(\d+)(?:$|[:_])", re.IGNORECASE)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|secret|token|prompt|raw[_-]?response|"
    r"request[_-]?headers|access[_-]?token)",
    re.IGNORECASE,
)
_GENERATION_KEY_PREFIXES = ("llm_", "scheduled_", "background_")
_META_KEYS = {
    "schema_version",
    "current_tick",
    "world_seed",
    "current_story_day",
    "last_completed_story_day",
    "shift_phase",
    "player_shift_income",
}


class WorldDiagnosticsError(ValueError):
    """Raised when a world cannot be read as a supported SQLite save."""


def _redact(value: Any, *, key: str = "") -> Any:
    """Copy JSON-shaped data while removing fields that could contain secrets."""

    if _SENSITIVE_KEY_RE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(name): _redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key=key) for item in value]
    return value


def _json_value(raw: Any) -> Any:
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return str(raw)


def _event_day(event: dict[str, Any]) -> int | None:
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("story_day", "day_index", "day"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
        for key in ("scene_id", "event_key"):
            match = _DAY_RE.search(str(payload.get(key, "")))
            if match:
                return int(match.group(1))
    tick = event.get("tick")
    if isinstance(tick, int) and tick >= 0:
        return tick // 1_440 + 1
    return None


def _read_jsonl(path: Path | None, *, day: int | None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    malformed = 0
    if path is None:
        return {"path": None, "records": records, "malformed_lines": malformed}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                malformed += 1
                continue
            if not isinstance(value, dict):
                malformed += 1
                continue
            record_day = value.get("story_day", value.get("day"))
            if day is not None and record_day != day:
                continue
            records.append(_redact(value))
    return {
        "path": path.name,
        "records": records,
        "malformed_lines": malformed,
    }


def _day_filter(day: int | None) -> None:
    if day is not None and (isinstance(day, bool) or day < 1):
        raise WorldDiagnosticsError("day must be a positive integer")


def _rows_as_events(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            "SELECT event_id, tick, event_type, actor_id, target_id, payload_json "
            "FROM events ORDER BY event_id"
        ).fetchall()
    except sqlite3.Error as exc:
        raise WorldDiagnosticsError("database has no readable events table") from exc
    return [
        {
            "event_id": int(row[0]),
            "tick": int(row[1]),
            "event_type": str(row[2]),
            "actor_id": row[3],
            "target_id": row[4],
            "payload": _redact(_json_value(row[5])),
        }
        for row in rows
    ]


def _graph_records(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            "SELECT day_index, generation_version, status, source_tick, "
            "source_event_ids_json, graph_json, error_code, attempt_count "
            "FROM daily_story_graphs ORDER BY day_index, generation_version"
        ).fetchall()
    except sqlite3.Error:
        return []
    records: list[dict[str, Any]] = []
    for row in rows:
        graph = _json_value(row[5]) if row[5] else None
        node_count = len(graph.get("nodes", [])) if isinstance(graph, dict) else 0
        records.append(
            {
                "day": int(row[0]),
                "generation_version": str(row[1]),
                "status": str(row[2]),
                "source_tick": int(row[3]),
                "source_event_ids": _redact(_json_value(row[4])),
                "node_count": node_count,
                "error_code": row[6],
                "attempt_count": int(row[7]),
            }
        )
    return records


def _progress_records(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            "SELECT day_index, generation_version, status, current_node_id, "
            "committed_branch_count FROM daily_story_progress "
            "ORDER BY day_index, generation_version"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "day": int(row[0]),
            "generation_version": str(row[1]),
            "status": str(row[2]),
            "current_node_id": row[3],
            "committed_branch_count": int(row[4]),
        }
        for row in rows
    ]


def _memory_records(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            "SELECT m.memory_id, m.agent_id, m.event_id, e.tick, "
            "m.importance, m.summary, m.tags_json, m.source_type, m.confidence, "
            "m.visibility, m.archived, m.canonical_key "
            "FROM memories AS m JOIN events AS e ON e.event_id = m.event_id "
            "ORDER BY m.memory_id"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "memory_id": int(row[0]),
            "agent_id": str(row[1]),
            "event_id": int(row[2]),
            "tick": int(row[3]),
            "importance": float(row[4]),
            "summary": str(row[5]),
            "tags": _redact(_json_value(row[6])),
            "source_type": str(row[7]),
            "confidence": float(row[8]),
            "visibility": str(row[9]),
            "archived": bool(row[10]),
            "canonical_key": row[11],
        }
        for row in rows
    ]


def _metadata_day(key: str) -> int | None:
    """Extract the day suffix used by generation receipts, when present."""

    match = re.search(r":(\d+)$", key)
    if match:
        return int(match.group(1))
    match = _DAY_RE.search(key)
    return None if match is None else int(match.group(1))


def _meta_records(connection: sqlite3.Connection, *, day: int | None = None) -> dict[str, Any]:
    try:
        rows = connection.execute("SELECT key, value FROM world_meta ORDER BY key").fetchall()
    except sqlite3.Error:
        return {}
    result: dict[str, Any] = {}
    for key, raw in rows:
        key = str(key)
        if key not in _META_KEYS and not key.startswith(_GENERATION_KEY_PREFIXES):
            continue
        if day is not None and key.startswith(_GENERATION_KEY_PREFIXES):
            metadata_day = _metadata_day(key)
            if metadata_day is not None and metadata_day != day:
                continue
        result[key] = _redact(_json_value(raw), key=key)
    return result


def _days_from(*groups: Iterable[int]) -> list[int]:
    values = {int(day) for group in groups for day in group if int(day) >= 1}
    return sorted(values)


def inspect_world_database(
    database: str | Path,
    *,
    day: int | None = None,
    timing_log: str | Path | None = None,
    dialogue_log: str | Path | None = None,
) -> dict[str, Any]:
    """Return a secret-free, read-only diagnostic/replay report.

    ``timing_log`` and ``dialogue_log`` are optional JSONL paths.  Database
    transcripts remain authoritative; supplied logs are included separately so
    a truncated or rotated log can never hide what was persisted in SQLite.
    """

    _day_filter(day)
    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise WorldDiagnosticsError(f"world database was not found: {path.name}")
    try:
        uri = path.as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise WorldDiagnosticsError("world database could not be opened read-only") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        events = _rows_as_events(connection)
        memories = _memory_records(connection)
        graphs = _graph_records(connection)
        progress = _progress_records(connection)
        metadata = _meta_records(connection, day=day)
        try:
            current_tick = int(dict(connection.execute(
                "SELECT value FROM world_meta WHERE key = 'current_tick'"
            ).fetchone() or {"value": "0"})["value"])
        except (sqlite3.Error, TypeError, ValueError):
            current_tick = 0
    finally:
        connection.close()

    event_days = {int(_event_day(item) or 0) for item in events}
    graph_days = {int(item["day"]) for item in graphs}
    memory_by_event = {int(item["event_id"]): item for item in memories}
    event_by_id = {int(item["event_id"]): item for item in events}
    transcripts = [
        item for item in events
        if item["event_type"] == "dialogue_transcript"
        and isinstance(item.get("payload"), dict)
    ]
    if day is not None:
        events = [item for item in events if _event_day(item) == day]
        transcripts = [item for item in transcripts if _event_day(item) == day]
        memories = [item for item in memories if _event_day(event_by_id.get(item["event_id"], {})) == day]
        graphs = [item for item in graphs if item["day"] == day]
        progress = [item for item in progress if item["day"] == day]

    db_transcripts = [
        {
            "event_id": item["event_id"],
            "story_day": item["payload"].get("story_day"),
            "scene_id": item["payload"].get("scene_id"),
            "lines": item["payload"].get("lines", []),
        }
        for item in transcripts
    ]
    timing = _read_jsonl(
        Path(timing_log).expanduser().resolve() if timing_log is not None else None,
        day=day,
    )
    dialogue = _read_jsonl(
        Path(dialogue_log).expanduser().resolve() if dialogue_log is not None else None,
        day=day,
    )
    days = _days_from(
        (day,) if day is not None else (),
        event_days,
        graph_days,
        [item["day"] for item in progress],
        [item.get("story_day") for item in db_transcripts if isinstance(item.get("story_day"), int)],
    )
    return {
        "report_version": REPORT_VERSION,
        "database": {
            "filename": path.name,
            "schema_version": metadata.get("schema_version"),
            "current_tick": current_tick,
        },
        "days": days,
        "summary": {
            "event_count": len(events),
            "dialogue_transcript_count": len(db_transcripts),
            "memory_count": len(memories),
            "generation_graph_count": len(graphs),
            "timing_record_count": len(timing["records"]),
        },
        "events": events,
        "dialogue_transcripts": db_transcripts,
        "memories": memories,
        "generation": {
            "graphs": graphs,
            "progress": progress,
            "metadata": metadata,
        },
        "logs": {"timing": timing, "dialogue": dialogue},
    }
