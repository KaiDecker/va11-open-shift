from __future__ import annotations

import json
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


SCHEMA_VERSION = 2


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorldStore:
    """SQLite persistence boundary for all authoritative world state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
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
