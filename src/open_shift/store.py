from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import (
    AgentState,
    Commitment,
    Goal,
    GoalStatus,
    Invitation,
    Memory,
    Relationship,
    ScheduledEvent,
    StoryArc,
)


SCHEMA_VERSION = 3
_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorldStore:
    """SQLite persistence boundary for all authoritative world state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Multiple bridge workers can finish a scene while the next day's
        # skeleton is being persisted. WAL handles readers, but writers still
        # need a bounded wait instead of failing the player's READY click.
        self._conn = sqlite3.connect(self.path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._transaction_depth = 0
        self._create_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> WorldStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Nestable transaction used to atomically commit one world action."""

        outermost = self._transaction_depth == 0
        if outermost:
            self._conn.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield
        except BaseException:
            self._transaction_depth -= 1
            if outermost:
                self._conn.rollback()
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                self._conn.commit()

    @contextmanager
    def _write_scope(self) -> Iterator[None]:
        if self._transaction_depth > 0:
            yield
        else:
            with self._conn:
                yield

    def _create_schema(self) -> None:
        with self._write_scope():
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS world_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    money INTEGER NOT NULL CHECK (money >= 0),
                    fatigue REAL NOT NULL CHECK (fatigue >= 0 AND fatigue <= 1),
                    mood TEXT NOT NULL,
                    daily_wake_minute INTEGER NOT NULL CHECK (
                        daily_wake_minute >= 0 AND daily_wake_minute < 1440
                    )
                );

                CREATE TABLE IF NOT EXISTS relationships (
                    source_id TEXT NOT NULL REFERENCES agents(agent_id),
                    target_id TEXT NOT NULL REFERENCES agents(agent_id),
                    trust REAL NOT NULL CHECK (trust >= -1 AND trust <= 1),
                    warmth REAL NOT NULL CHECK (warmth >= -1 AND warmth <= 1),
                    debt INTEGER NOT NULL,
                    PRIMARY KEY (source_id, target_id),
                    CHECK (source_id <> target_id)
                );

                CREATE TABLE IF NOT EXISTS goals (
                    goal_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
                    kind TEXT NOT NULL,
                    target_id TEXT REFERENCES agents(agent_id),
                    target_value REAL NOT NULL,
                    priority REAL NOT NULL CHECK (priority >= 0 AND priority <= 1),
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tick INTEGER NOT NULL CHECK (tick >= 0),
                    event_type TEXT NOT NULL,
                    actor_id TEXT,
                    target_id TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_tick
                ON events(tick, event_id);

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
                    event_id INTEGER NOT NULL REFERENCES events(event_id),
                    importance REAL NOT NULL CHECK (importance >= 0 AND importance <= 1),
                    summary TEXT NOT NULL,
                    tags_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memories_agent
                ON memories(agent_id, memory_id);

                CREATE TABLE IF NOT EXISTS invitations (
                    invitation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter_id TEXT NOT NULL REFERENCES agents(agent_id),
                    invitee_id TEXT NOT NULL REFERENCES agents(agent_id),
                    location TEXT NOT NULL,
                    proposed_tick INTEGER NOT NULL CHECK (proposed_tick >= 0),
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'accepted', 'declined')
                    ),
                    created_event_id INTEGER NOT NULL REFERENCES events(event_id),
                    CHECK (inviter_id <> invitee_id)
                );

                CREATE INDEX IF NOT EXISTS idx_invitations_participants
                ON invitations(inviter_id, invitee_id, status, proposed_tick);

                CREATE TABLE IF NOT EXISTS commitments (
                    commitment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id TEXT NOT NULL REFERENCES agents(agent_id),
                    target_id TEXT NOT NULL REFERENCES agents(agent_id),
                    due_tick INTEGER NOT NULL CHECK (due_tick >= 0),
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'fulfilled', 'broken')
                    ),
                    created_event_id INTEGER NOT NULL REFERENCES events(event_id),
                    resolved_event_id INTEGER REFERENCES events(event_id),
                    CHECK (actor_id <> target_id)
                );

                CREATE INDEX IF NOT EXISTS idx_commitments_actor_due
                ON commitments(actor_id, status, due_tick);

                CREATE TABLE IF NOT EXISTS story_arcs (
                    arc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL REFERENCES agents(agent_id),
                    target_id TEXT REFERENCES agents(agent_id),
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'resolved')),
                    progress INTEGER NOT NULL CHECK (progress >= 0),
                    required_progress INTEGER NOT NULL CHECK (required_progress > 0),
                    created_tick INTEGER NOT NULL CHECK (created_tick >= 0),
                    resolved_tick INTEGER CHECK (resolved_tick >= created_tick)
                );

                CREATE INDEX IF NOT EXISTS idx_story_arcs_owner
                ON story_arcs(owner_id, status, arc_id);

                CREATE TABLE IF NOT EXISTS scheduled_events (
                    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tick INTEGER NOT NULL CHECK (tick >= 0),
                    event_type TEXT NOT NULL,
                    actor_id TEXT REFERENCES agents(agent_id),
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_schedule_due
                ON scheduled_events(tick, schedule_id);

                CREATE TABLE IF NOT EXISTS daily_story_graphs (
                    day_index INTEGER NOT NULL CHECK (day_index >= 1),
                    generation_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('generating', 'ready', 'failed')
                    ),
                    source_tick INTEGER NOT NULL CHECK (source_tick >= 0),
                    source_event_ids_json TEXT NOT NULL,
                    graph_json TEXT,
                    error_code TEXT,
                    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
                    PRIMARY KEY (day_index, generation_version),
                    CHECK (
                        (status = 'ready' AND graph_json IS NOT NULL AND error_code IS NULL)
                        OR (status = 'generating' AND graph_json IS NULL AND error_code IS NULL)
                        OR (status = 'failed' AND graph_json IS NULL AND error_code IS NOT NULL)
                    )
                );

                CREATE TABLE IF NOT EXISTS daily_story_progress (
                    day_index INTEGER NOT NULL,
                    generation_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'completed')),
                    current_node_id TEXT,
                    committed_branch_count INTEGER NOT NULL DEFAULT 0
                        CHECK (committed_branch_count >= 0),
                    PRIMARY KEY (day_index, generation_version),
                    FOREIGN KEY (day_index, generation_version)
                        REFERENCES daily_story_graphs(day_index, generation_version),
                    CHECK (
                        (status = 'active' AND current_node_id IS NOT NULL)
                        OR (status = 'completed' AND current_node_id IS NULL)
                    )
                );

                CREATE TABLE IF NOT EXISTS story_branch_commits (
                    day_index INTEGER NOT NULL,
                    generation_version TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    arrival_node_id TEXT NOT NULL,
                    result_node_id TEXT NOT NULL,
                    category TEXT NOT NULL CHECK (
                        category IN ('exact', 'acceptable', 'wrong', 'special')
                    ),
                    service_event_id INTEGER NOT NULL UNIQUE REFERENCES events(event_id),
                    income_delta INTEGER NOT NULL CHECK (income_delta >= 0),
                    PRIMARY KEY (day_index, generation_version, order_id),
                    UNIQUE (order_id),
                    FOREIGN KEY (day_index, generation_version)
                        REFERENCES daily_story_graphs(day_index, generation_version)
                );
                """
            )
            self._conn.execute(
                """
                INSERT INTO world_meta(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO world_meta(key, value) VALUES('current_tick', '0')"
            )

    def set_meta(self, key: str, value: str | int) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO world_meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM world_meta WHERE key = ?", (key,)
        ).fetchone()
        return default if row is None else str(row["value"])

    @property
    def current_tick(self) -> int:
        return int(self.get_meta("current_tick", "0") or 0)

    def set_current_tick(self, tick: int) -> None:
        if tick < self.current_tick:
            raise ValueError("world time cannot move backwards")
        self.set_meta("current_tick", tick)

    def get_daily_story_graph(
        self, day_index: int, generation_version: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM daily_story_graphs
            WHERE day_index = ? AND generation_version = ?
            """,
            (day_index, generation_version),
        ).fetchone()
        if row is None:
            return None
        return {
            "day_index": int(row["day_index"]),
            "generation_version": str(row["generation_version"]),
            "status": str(row["status"]),
            "source_tick": int(row["source_tick"]),
            "source_event_ids": tuple(json.loads(row["source_event_ids_json"])),
            "graph": json.loads(row["graph_json"]) if row["graph_json"] else None,
            "error_code": row["error_code"],
            "attempt_count": int(row["attempt_count"]),
        }

    def migrate_incompatible_daily_story(
        self, day_index: int, generation_version: str
    ) -> dict[str, Any] | None:
        """Discard only an interrupted old-version shift and reset its gates.

        Story graphs are executable cursors, so replaying one from an older
        generation can skip the current opening/music flow.  The world events
        which produced the graph remain intact; only bridge-owned scenes,
        service effects, and dialogue memories for the active day are removed.
        Completed prior days are never touched.
        """
        old_rows = self._conn.execute(
            "SELECT * FROM daily_story_graphs WHERE day_index = ? AND generation_version <> ?",
            (day_index, generation_version),
        ).fetchall()
        if not old_rows:
            return None
        active_old = False
        for row in old_rows:
            progress = self._conn.execute(
                "SELECT status FROM daily_story_progress WHERE day_index = ? AND generation_version = ?",
                (day_index, row["generation_version"]),
            ).fetchone()
            if progress is None or progress[0] == "active":
                active_old = True
                break
        if not active_old:
            return None

        day_token = f"day_{day_index}_"
        scene_tokens = (
            f"opening_day_{day_index}",
            f"doorbell_day_{day_index}",
            f"pre_opening_day_{day_index}",
            f"music_selection_day_{day_index}",
            f"break_day_{day_index}",
            f"closing_day_{day_index}",
            f"settlement_day_{day_index}",
            day_token,
        )
        delete_event_ids: set[int] = set()
        for row in self._conn.execute(
            "SELECT service_event_id FROM story_branch_commits WHERE day_index = ?",
            (day_index,),
        ).fetchall():
            delete_event_ids.add(int(row[0]))
        event_rows = self._conn.execute(
            "SELECT event_id, event_type, payload_json FROM events"
        ).fetchall()
        for row in event_rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                payload = {}
            scene_id = str(payload.get("scene_id", "")) if isinstance(payload, dict) else ""
            if (
                isinstance(payload, dict)
                and payload.get("story_day") == day_index
                and row["event_type"] in {
                    "drink_served",
                    "agent_dialogue_completed",
                    "player_scene_ack",
                    "dialogue_provider_error",
                    "dialogue_provider_fallback",
                    "dialogue_transcript",
                }
            ) or any(token in scene_id for token in scene_tokens):
                delete_event_ids.add(int(row["event_id"]))
        # Remove the referencing commits before their service events; foreign
        # keys are enabled on every WorldStore connection.
        self._conn.execute("DELETE FROM story_branch_commits WHERE day_index = ?", (day_index,))
        if delete_event_ids:
            marks = ",".join("?" for _ in delete_event_ids)
            self._conn.execute(
                f"DELETE FROM memories WHERE event_id IN ({marks})",
                tuple(delete_event_ids),
            )
            self._conn.execute(
                f"DELETE FROM events WHERE event_id IN ({marks})",
                tuple(delete_event_ids),
            )
        # Remove every executable graph for this interrupted day, including a
        # partially-created target-version graph.  Keeping that graph while
        # deleting its progress would leave a ready graph with no cursor and
        # make the next launch ambiguous.
        self._conn.execute("DELETE FROM daily_story_progress WHERE day_index = ?", (day_index,))
        self._conn.execute("DELETE FROM daily_story_graphs WHERE day_index = ?", (day_index,))

        meta_rows = self._conn.execute("SELECT key FROM world_meta").fetchall()
        for row in meta_rows:
            key = str(row[0])
            raw_value = self.get_meta(key, "") or ""
            if key.startswith("bridge_ack_request:"):
                # ACK request ids are client-session scoped.  Keep receipts
                # from completed days, but remove current-day requests when
                # their scene id or payload identifies this migration.
                if any(token in raw_value for token in scene_tokens) or f'"story_day":{day_index}' in raw_value:
                    self._conn.execute("DELETE FROM world_meta WHERE key = ?", (key,))
                continue
            try:
                parsed_value = json.loads(raw_value)
            except (TypeError, ValueError):
                parsed_value = None
            parsed_text = json.dumps(parsed_value, ensure_ascii=False, separators=(",", ":")) if parsed_value is not None else raw_value
            if key.startswith((
                "bridge_ack:", "bridge_ack_request:", "bridge_open:",
                "bridge_scene:", "bridge_scene_payload:", "bridge_order:",
                "bridge_order_request:", "story_scene_node:",
                "story_materialized_scene:",
            )):
                value = key.split(":", 1)[1]
                contains_day = (
                    f'"story_day":{day_index}' in raw_value
                    or f'"day_index":{day_index}' in raw_value
                )
                if (
                    any(token in value for token in scene_tokens)
                    or any(token in raw_value for token in scene_tokens)
                    or any(token in parsed_text for token in scene_tokens)
                    or contains_day
                    or f"order_day_{day_index}_" in value
                    or f"order_day_{day_index}_" in raw_value
                ):
                    self._conn.execute("DELETE FROM world_meta WHERE key = ?", (key,))
            elif key in {
                f"break_pending_day_{day_index}",
                f"music_selected_day_{day_index}",
            }:
                self._conn.execute("DELETE FROM world_meta WHERE key = ?", (key,))

        self._conn.execute(
            "INSERT INTO world_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("player_shift_income", "0"),
        )
        self._conn.execute(
            "INSERT INTO world_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("shift_phase", "playing"),
        )
        return {
            "day": day_index,
            "old_versions": [str(row["generation_version"]) for row in old_rows],
            "deleted_event_count": len(delete_event_ids),
        }

    def list_daily_story_graphs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT day_index, generation_version
            FROM daily_story_graphs
            ORDER BY day_index, generation_version
            """
        ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = self.get_daily_story_graph(
                int(row["day_index"]), str(row["generation_version"])
            )
            if record is None:
                raise RuntimeError("daily story graph disappeared while listing")
            records.append(record)
        return records

    def begin_daily_story_graph(
        self,
        day_index: int,
        generation_version: str,
        source_tick: int,
        source_event_ids: Iterable[int],
    ) -> dict[str, Any]:
        event_ids = tuple(source_event_ids)
        if (
            day_index < 1
            or source_tick < 0
            or not generation_version
            or not 1 <= len(event_ids) <= 3
            or len(set(event_ids)) != len(event_ids)
            or any(
                isinstance(event_id, bool)
                or not isinstance(event_id, int)
                or event_id < 1
                for event_id in event_ids
            )
        ):
            raise ValueError("daily story graph generation input was invalid")
        with self.transaction():
            existing = self.get_daily_story_graph(day_index, generation_version)
            if existing is None:
                self._conn.execute(
                    """
                    INSERT INTO daily_story_graphs(
                        day_index, generation_version, status, source_tick,
                        source_event_ids_json, graph_json, error_code, attempt_count
                    ) VALUES(?, ?, 'generating', ?, ?, NULL, NULL, 1)
                    """,
                    (
                        day_index,
                        generation_version,
                        source_tick,
                        _json_dump(event_ids),
                    ),
                )
            elif existing["status"] != "ready":
                if (
                    existing["source_tick"] != source_tick
                    or existing["source_event_ids"] != event_ids
                ):
                    raise ValueError("daily story graph recovery source changed")
                self._conn.execute(
                    """
                    UPDATE daily_story_graphs
                    SET status = 'generating', graph_json = NULL,
                        error_code = NULL, attempt_count = attempt_count + 1
                    WHERE day_index = ? AND generation_version = ?
                    """,
                    (day_index, generation_version),
                )
        record = self.get_daily_story_graph(day_index, generation_version)
        assert record is not None
        return record

    def complete_daily_story_graph(
        self,
        day_index: int,
        generation_version: str,
        graph: Mapping[str, Any],
    ) -> None:
        from .story_graph import DailyStoryGraph

        validated = DailyStoryGraph.from_dict(graph)
        if (
            validated.day_index != day_index
            or validated.generation_version != generation_version
        ):
            raise ValueError("daily story graph identity did not match its record")
        with self.transaction():
            record = self.get_daily_story_graph(day_index, generation_version)
            if record is None or (
                validated.source_tick != record["source_tick"]
                or validated.source_event_ids != record["source_event_ids"]
            ):
                raise ValueError("daily story graph source did not match its record")
            cursor = self._conn.execute(
                """
                UPDATE daily_story_graphs
                SET status = 'ready', graph_json = ?, error_code = NULL
                WHERE day_index = ? AND generation_version = ?
                  AND status = 'generating'
                """,
                (_json_dump(validated.to_dict()), day_index, generation_version),
            )
            if cursor.rowcount != 1:
                raise ValueError("daily story graph was not generating")
            cursor = self._conn.execute(
                """
                INSERT INTO daily_story_progress(
                    day_index, generation_version, status, current_node_id,
                    committed_branch_count
                ) VALUES(?, ?, 'active', ?, 0)
                ON CONFLICT(day_index, generation_version) DO NOTHING
                """,
                (day_index, generation_version, validated.entry_node_id),
            )

    def fail_daily_story_graph(
        self, day_index: int, generation_version: str, error_code: str
    ) -> None:
        if not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("daily story graph error_code was invalid")
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE daily_story_graphs
                SET status = 'failed', graph_json = NULL, error_code = ?
                WHERE day_index = ? AND generation_version = ?
                  AND status = 'generating'
                """,
                (error_code, day_index, generation_version),
            )
            if cursor.rowcount != 1:
                raise ValueError("daily story graph was not generating")

    def get_daily_story_progress(
        self, day_index: int, generation_version: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM daily_story_progress
            WHERE day_index = ? AND generation_version = ?
            """,
            (day_index, generation_version),
        ).fetchone()
        if row is None:
            return None
        return {
            "day_index": int(row["day_index"]),
            "generation_version": str(row["generation_version"]),
            "status": str(row["status"]),
            "current_node_id": row["current_node_id"],
            "committed_branch_count": int(row["committed_branch_count"]),
        }

    def advance_daily_story_cursor(
        self,
        day_index: int,
        generation_version: str,
        expected_node_id: str,
        next_node_id: str | None,
    ) -> None:
        status = "completed" if next_node_id is None else "active"
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE daily_story_progress
                SET status = ?, current_node_id = ?
                WHERE day_index = ? AND generation_version = ?
                  AND status = 'active' AND current_node_id = ?
                """,
                (
                    status,
                    next_node_id,
                    day_index,
                    generation_version,
                    expected_node_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("daily story cursor did not match the expected node")

    def get_story_branch_commit(self, order_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM story_branch_commits WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "day_index": int(row["day_index"]),
            "generation_version": str(row["generation_version"]),
            "order_id": str(row["order_id"]),
            "arrival_node_id": str(row["arrival_node_id"]),
            "result_node_id": str(row["result_node_id"]),
            "category": str(row["category"]),
            "service_event_id": int(row["service_event_id"]),
            "income_delta": int(row["income_delta"]),
        }

    def record_story_branch_commit(
        self,
        *,
        day_index: int,
        generation_version: str,
        order_id: str,
        arrival_node_id: str,
        result_node_id: str,
        category: str,
        service_event_id: int,
        income_delta: int,
    ) -> None:
        if category not in {"exact", "acceptable", "wrong", "special"}:
            raise ValueError("story branch category was invalid")
        if service_event_id < 1 or income_delta < 0:
            raise ValueError("story branch effects were invalid")
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO story_branch_commits(
                    day_index, generation_version, order_id, arrival_node_id,
                    result_node_id, category, service_event_id, income_delta
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day_index,
                    generation_version,
                    order_id,
                    arrival_node_id,
                    result_node_id,
                    category,
                    service_event_id,
                    income_delta,
                ),
            )
            cursor = self._conn.execute(
                """
                UPDATE daily_story_progress
                SET committed_branch_count = committed_branch_count + 1
                WHERE day_index = ? AND generation_version = ?
                """,
                (day_index, generation_version),
            )
            if cursor.rowcount != 1:
                raise ValueError("daily story progress was missing for branch commit")

    def list_story_branch_commits(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT order_id FROM story_branch_commits ORDER BY service_event_id"
        ).fetchall()
        commits: list[dict[str, Any]] = []
        for row in rows:
            commit = self.get_story_branch_commit(str(row["order_id"]))
            if commit is None:
                raise RuntimeError("story branch commit disappeared while listing")
            commits.append(commit)
        return commits

    def add_agent(self, agent: AgentState) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO agents(
                    agent_id, display_name, location, money, fatigue, mood,
                    daily_wake_minute
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent.agent_id,
                    agent.display_name,
                    agent.location,
                    agent.money,
                    agent.fatigue,
                    agent.mood,
                    agent.daily_wake_minute,
                ),
            )

    @staticmethod
    def _agent_from_row(row: sqlite3.Row) -> AgentState:
        return AgentState(
            agent_id=row["agent_id"],
            display_name=row["display_name"],
            location=row["location"],
            money=int(row["money"]),
            fatigue=float(row["fatigue"]),
            mood=row["mood"],
            daily_wake_minute=int(row["daily_wake_minute"]),
        )

    def get_agent(self, agent_id: str) -> AgentState | None:
        row = self._conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return None if row is None else self._agent_from_row(row)

    def list_agents(self) -> list[AgentState]:
        rows = self._conn.execute("SELECT * FROM agents ORDER BY agent_id").fetchall()
        return [self._agent_from_row(row) for row in rows]

    def update_agent(self, agent: AgentState) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE agents
                SET display_name = ?, location = ?, money = ?, fatigue = ?,
                    mood = ?, daily_wake_minute = ?
                WHERE agent_id = ?
                """,
                (
                    agent.display_name,
                    agent.location,
                    agent.money,
                    agent.fatigue,
                    agent.mood,
                    agent.daily_wake_minute,
                    agent.agent_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown agent: {agent.agent_id}")

    def upsert_relationship(self, relationship: Relationship) -> None:
        if relationship.source_id == relationship.target_id:
            raise ValueError("an agent cannot have a relationship with itself")
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO relationships(source_id, target_id, trust, warmth, debt)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id) DO UPDATE SET
                    trust = excluded.trust,
                    warmth = excluded.warmth,
                    debt = excluded.debt
                """,
                (
                    relationship.source_id,
                    relationship.target_id,
                    relationship.trust,
                    relationship.warmth,
                    relationship.debt,
                ),
            )

    def get_relationship(self, source_id: str, target_id: str) -> Relationship:
        row = self._conn.execute(
            """
            SELECT * FROM relationships WHERE source_id = ? AND target_id = ?
            """,
            (source_id, target_id),
        ).fetchone()
        if row is None:
            return Relationship(source_id=source_id, target_id=target_id)
        return Relationship(
            source_id=row["source_id"],
            target_id=row["target_id"],
            trust=float(row["trust"]),
            warmth=float(row["warmth"]),
            debt=int(row["debt"]),
        )

    def list_relationships(self, source_id: str | None = None) -> list[Relationship]:
        if source_id is None:
            rows = self._conn.execute(
                "SELECT * FROM relationships ORDER BY source_id, target_id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM relationships
                WHERE source_id = ? ORDER BY target_id
                """,
                (source_id,),
            ).fetchall()
        return [
            Relationship(
                source_id=row["source_id"],
                target_id=row["target_id"],
                trust=float(row["trust"]),
                warmth=float(row["warmth"]),
                debt=int(row["debt"]),
            )
            for row in rows
        ]

    def add_goal(self, goal: Goal) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO goals(
                    goal_id, agent_id, kind, target_id, target_value,
                    priority, status, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal.goal_id,
                    goal.agent_id,
                    goal.kind,
                    goal.target_id,
                    goal.target_value,
                    goal.priority,
                    goal.status.value,
                    _json_dump(goal.metadata),
                ),
            )

    @staticmethod
    def _goal_from_row(row: sqlite3.Row) -> Goal:
        return Goal(
            goal_id=row["goal_id"],
            agent_id=row["agent_id"],
            kind=row["kind"],
            target_id=row["target_id"],
            target_value=float(row["target_value"]),
            priority=float(row["priority"]),
            status=GoalStatus(row["status"]),
            metadata=json.loads(row["metadata_json"]),
        )

    def list_goals(
        self, agent_id: str | None = None, status: GoalStatus | None = None
    ) -> list[Goal]:
        clauses: list[str] = []
        values: list[str] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            values.append(agent_id)
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM goals{where} ORDER BY goal_id", values
        ).fetchall()
        return [self._goal_from_row(row) for row in rows]

    def set_goal_status(self, goal_id: str, status: GoalStatus) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                "UPDATE goals SET status = ? WHERE goal_id = ?",
                (status.value, goal_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown goal: {goal_id}")

    def append_event(
        self,
        tick: int,
        event_type: str,
        actor_id: str | None,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                INSERT INTO events(tick, event_type, actor_id, target_id, payload_json)
                VALUES(?, ?, ?, ?, ?)
                """,
                (tick, event_type, actor_id, target_id, _json_dump(payload or {})),
            )
            return int(cursor.lastrowid)

    def list_events(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY event_id"
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "tick": int(row["tick"]),
                "event_type": row["event_type"],
                "actor_id": row["actor_id"],
                "target_id": row["target_id"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def append_memory(
        self,
        agent_id: str,
        event_id: int,
        importance: float,
        summary: str,
        tags: Iterable[str],
    ) -> int:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                INSERT INTO memories(
                    agent_id, event_id, importance, summary, tags_json
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (agent_id, event_id, importance, summary, _json_dump(sorted(tags))),
            )
            return int(cursor.lastrowid)

    def list_memories(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        if agent_id is None:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY memory_id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM memories WHERE agent_id = ? ORDER BY memory_id
                """,
                (agent_id,),
            ).fetchall()
        return [
            {
                "memory_id": int(row["memory_id"]),
                "agent_id": row["agent_id"],
                "event_id": int(row["event_id"]),
                "importance": float(row["importance"]),
                "summary": row["summary"],
                "tags": json.loads(row["tags_json"]),
            }
            for row in rows
        ]

    def retrieve_memories(
        self,
        agent_id: str,
        tick: int,
        *,
        tags: Iterable[str] = (),
        limit: int = 8,
        character_budget: int = 1_200,
    ) -> list[Memory]:
        """Return a deterministic, private and bounded memory context."""

        wanted = set(tags)
        rows = self._conn.execute(
            """
            SELECT m.*, e.tick
            FROM memories AS m
            JOIN events AS e ON e.event_id = m.event_id
            WHERE m.agent_id = ? AND e.tick <= ?
            ORDER BY m.memory_id
            """,
            (agent_id, tick),
        ).fetchall()
        scored: list[tuple[float, int, sqlite3.Row, tuple[str, ...]]] = []
        for row in rows:
            memory_tags = tuple(json.loads(row["tags_json"]))
            age_days = max(0.0, (tick - int(row["tick"])) / 1_440)
            recency = 1.0 / (1.0 + age_days)
            relevance = len(wanted.intersection(memory_tags))
            score = float(row["importance"]) * 2.0 + recency + relevance
            scored.append((score, int(row["memory_id"]), row, memory_tags))
        scored.sort(key=lambda item: (-item[0], -item[1]))

        selected: list[Memory] = []
        used = 0
        for _, _, row, memory_tags in scored:
            summary = str(row["summary"])
            if selected and used + len(summary) > character_budget:
                continue
            if not selected and len(summary) > character_budget:
                summary = summary[:character_budget]
            selected.append(
                Memory(
                    memory_id=int(row["memory_id"]),
                    event_id=int(row["event_id"]),
                    tick=int(row["tick"]),
                    importance=float(row["importance"]),
                    summary=summary,
                    tags=memory_tags,
                )
            )
            used += len(summary)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _invitation_from_row(row: sqlite3.Row) -> Invitation:
        return Invitation(
            invitation_id=int(row["invitation_id"]),
            inviter_id=row["inviter_id"],
            invitee_id=row["invitee_id"],
            location=row["location"],
            proposed_tick=int(row["proposed_tick"]),
            status=row["status"],
            created_event_id=int(row["created_event_id"]),
        )

    def add_invitation(
        self,
        inviter_id: str,
        invitee_id: str,
        location: str,
        proposed_tick: int,
        created_event_id: int,
    ) -> int:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                INSERT INTO invitations(
                    inviter_id, invitee_id, location, proposed_tick, status,
                    created_event_id
                ) VALUES(?, ?, ?, ?, 'pending', ?)
                """,
                (inviter_id, invitee_id, location, proposed_tick, created_event_id),
            )
            return int(cursor.lastrowid)

    def get_invitation(self, invitation_id: int) -> Invitation | None:
        row = self._conn.execute(
            "SELECT * FROM invitations WHERE invitation_id = ?", (invitation_id,)
        ).fetchone()
        return None if row is None else self._invitation_from_row(row)

    def list_invitations(
        self, agent_id: str | None = None, status: str | None = None
    ) -> list[Invitation]:
        clauses: list[str] = []
        values: list[object] = []
        if agent_id is not None:
            clauses.append("(inviter_id = ? OR invitee_id = ?)")
            values.extend((agent_id, agent_id))
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM invitations{where} ORDER BY invitation_id", values
        ).fetchall()
        return [self._invitation_from_row(row) for row in rows]

    def set_invitation_status(self, invitation_id: int, status: str) -> None:
        if status not in {"accepted", "declined"}:
            raise ValueError("invalid resolved invitation status")
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE invitations SET status = ?
                WHERE invitation_id = ? AND status = 'pending'
                """,
                (status, invitation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown or resolved invitation: {invitation_id}")

    @staticmethod
    def _commitment_from_row(row: sqlite3.Row) -> Commitment:
        return Commitment(
            commitment_id=int(row["commitment_id"]),
            actor_id=row["actor_id"],
            target_id=row["target_id"],
            due_tick=int(row["due_tick"]),
            status=row["status"],
            created_event_id=int(row["created_event_id"]),
            resolved_event_id=(
                None
                if row["resolved_event_id"] is None
                else int(row["resolved_event_id"])
            ),
        )

    def add_commitment(
        self,
        actor_id: str,
        target_id: str,
        due_tick: int,
        created_event_id: int,
    ) -> int:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                INSERT INTO commitments(
                    actor_id, target_id, due_tick, status, created_event_id
                ) VALUES(?, ?, ?, 'pending', ?)
                """,
                (actor_id, target_id, due_tick, created_event_id),
            )
            return int(cursor.lastrowid)

    def list_commitments(
        self, agent_id: str | None = None, status: str | None = None
    ) -> list[Commitment]:
        clauses: list[str] = []
        values: list[object] = []
        if agent_id is not None:
            clauses.append("(actor_id = ? OR target_id = ?)")
            values.extend((agent_id, agent_id))
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM commitments{where} ORDER BY commitment_id", values
        ).fetchall()
        return [self._commitment_from_row(row) for row in rows]

    def resolve_commitment(
        self, commitment_id: int, status: str, event_id: int
    ) -> None:
        if status not in {"fulfilled", "broken"}:
            raise ValueError("invalid resolved commitment status")
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE commitments SET status = ?, resolved_event_id = ?
                WHERE commitment_id = ? AND status = 'pending'
                """,
                (status, event_id, commitment_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown or resolved commitment: {commitment_id}")

    @staticmethod
    def _story_arc_from_row(row: sqlite3.Row) -> StoryArc:
        return StoryArc(
            arc_id=int(row["arc_id"]),
            owner_id=row["owner_id"],
            target_id=row["target_id"],
            kind=row["kind"],
            status=row["status"],
            progress=int(row["progress"]),
            required_progress=int(row["required_progress"]),
            created_tick=int(row["created_tick"]),
            resolved_tick=(
                None if row["resolved_tick"] is None else int(row["resolved_tick"])
            ),
        )

    def add_story_arc(
        self,
        owner_id: str,
        target_id: str | None,
        kind: str,
        created_tick: int,
        *,
        required_progress: int = 3,
    ) -> int:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                INSERT INTO story_arcs(
                    owner_id, target_id, kind, status, progress,
                    required_progress, created_tick
                ) VALUES(?, ?, ?, 'active', 0, ?, ?)
                """,
                (owner_id, target_id, kind, required_progress, created_tick),
            )
            return int(cursor.lastrowid)

    def list_story_arcs(
        self, owner_id: str | None = None, status: str | None = None
    ) -> list[StoryArc]:
        clauses: list[str] = []
        values: list[object] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            values.append(owner_id)
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM story_arcs{where} ORDER BY arc_id", values
        ).fetchall()
        return [self._story_arc_from_row(row) for row in rows]

    def advance_story_arcs(
        self, owner_id: str, target_id: str | None, tick: int
    ) -> list[StoryArc]:
        resolved: list[StoryArc] = []
        with self._write_scope():
            rows = self._conn.execute(
                """
                SELECT * FROM story_arcs
                WHERE owner_id = ? AND status = 'active'
                  AND (target_id IS NULL OR target_id = ?)
                ORDER BY arc_id
                """,
                (owner_id, target_id),
            ).fetchall()
            for row in rows:
                progress = int(row["progress"]) + 1
                status = (
                    "resolved"
                    if progress >= int(row["required_progress"])
                    else "active"
                )
                self._conn.execute(
                    """
                    UPDATE story_arcs
                    SET progress = ?, status = ?, resolved_tick = ?
                    WHERE arc_id = ?
                    """,
                    (
                        progress,
                        status,
                        tick if status == "resolved" else None,
                        row["arc_id"],
                    ),
                )
                if status == "resolved":
                    updated = self._conn.execute(
                        "SELECT * FROM story_arcs WHERE arc_id = ?", (row["arc_id"],)
                    ).fetchone()
                    resolved.append(self._story_arc_from_row(updated))
        return resolved

    def schedule_event(
        self,
        tick: int,
        event_type: str,
        actor_id: str | None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                INSERT INTO scheduled_events(tick, event_type, actor_id, payload_json)
                VALUES(?, ?, ?, ?)
                """,
                (tick, event_type, actor_id, _json_dump(payload or {})),
            )
            return int(cursor.lastrowid)

    def scheduled_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM scheduled_events"
        ).fetchone()
        return int(row["count"])

    def pop_next_scheduled(self, max_tick: int) -> ScheduledEvent | None:
        with self._write_scope():
            row = self._conn.execute(
                """
                SELECT * FROM scheduled_events
                WHERE tick <= ?
                ORDER BY tick, schedule_id
                LIMIT 1
                """,
                (max_tick,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM scheduled_events WHERE schedule_id = ?",
                (row["schedule_id"],),
            )
        return ScheduledEvent(
            schedule_id=int(row["schedule_id"]),
            tick=int(row["tick"]),
            event_type=row["event_type"],
            actor_id=row["actor_id"],
            payload=json.loads(row["payload_json"]),
        )

    def dump_state(self) -> dict[str, Any]:
        return {
            "current_tick": self.current_tick,
            "daily_story_graphs": self.list_daily_story_graphs(),
            "daily_story_progress": [
                progress
                for graph in self.list_daily_story_graphs()
                if (
                    progress := self.get_daily_story_progress(
                        graph["day_index"], graph["generation_version"]
                    )
                )
                is not None
            ],
            "story_branch_commits": self.list_story_branch_commits(),
            "agents": [
                {
                    "agent_id": agent.agent_id,
                    "display_name": agent.display_name,
                    "location": agent.location,
                    "money": agent.money,
                    "fatigue": round(agent.fatigue, 6),
                    "mood": agent.mood,
                    "daily_wake_minute": agent.daily_wake_minute,
                }
                for agent in self.list_agents()
            ],
            "relationships": [
                {
                    "source_id": rel.source_id,
                    "target_id": rel.target_id,
                    "trust": round(rel.trust, 6),
                    "warmth": round(rel.warmth, 6),
                    "debt": rel.debt,
                }
                for rel in self.list_relationships()
            ],
            "goals": [
                {
                    "goal_id": goal.goal_id,
                    "agent_id": goal.agent_id,
                    "kind": goal.kind,
                    "target_id": goal.target_id,
                    "target_value": goal.target_value,
                    "priority": goal.priority,
                    "status": goal.status.value,
                    "metadata": goal.metadata,
                }
                for goal in self.list_goals()
            ],
            "invitations": [
                {
                    "invitation_id": invitation.invitation_id,
                    "inviter_id": invitation.inviter_id,
                    "invitee_id": invitation.invitee_id,
                    "location": invitation.location,
                    "proposed_tick": invitation.proposed_tick,
                    "status": invitation.status,
                    "created_event_id": invitation.created_event_id,
                }
                for invitation in self.list_invitations()
            ],
            "commitments": [
                {
                    "commitment_id": commitment.commitment_id,
                    "actor_id": commitment.actor_id,
                    "target_id": commitment.target_id,
                    "due_tick": commitment.due_tick,
                    "status": commitment.status,
                    "created_event_id": commitment.created_event_id,
                    "resolved_event_id": commitment.resolved_event_id,
                }
                for commitment in self.list_commitments()
            ],
            "story_arcs": [
                {
                    "arc_id": arc.arc_id,
                    "owner_id": arc.owner_id,
                    "target_id": arc.target_id,
                    "kind": arc.kind,
                    "status": arc.status,
                    "progress": arc.progress,
                    "required_progress": arc.required_progress,
                    "created_tick": arc.created_tick,
                    "resolved_tick": arc.resolved_tick,
                }
                for arc in self.list_story_arcs()
            ],
        }
