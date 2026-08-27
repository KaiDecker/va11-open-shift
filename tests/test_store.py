from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from open_shift.models import AgentState, Relationship
from open_shift.store import WorldStore


class WorldStoreTests(unittest.TestCase):
    def test_agent_relationship_and_schedule_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(path) as store:
                store.add_agent(AgentState("a", "A", "home", 10, 0.2, "ok", 60))
                store.add_agent(AgentState("b", "B", "work", 20, 0.4, "ok", 90))
                store.upsert_relationship(Relationship("a", "b", 0.2, 0.3, 0))
                later = store.schedule_event(200, "agent_turn", "a")
                earlier = store.schedule_event(100, "agent_turn", "b")
                self.assertLess(later, earlier)
                event = store.pop_next_scheduled(150)
                self.assertIsNotNone(event)
                assert event is not None
                self.assertEqual(event.actor_id, "b")
                self.assertEqual(event.tick, 100)

            with WorldStore(path) as reopened:
                self.assertEqual([a.agent_id for a in reopened.list_agents()], ["a", "b"])
                relationship = reopened.get_relationship("a", "b")
                self.assertAlmostEqual(relationship.trust, 0.2)
                self.assertEqual(reopened.scheduled_count(), 1)

    def test_world_time_cannot_move_backwards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                store.set_current_tick(50)
                with self.assertRaises(ValueError):
                    store.set_current_tick(49)

    def test_schema_version_one_database_upgrades_without_losing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(path) as store:
                store.add_agent(AgentState("a", "A", "home", 10, 0.2, "ok", 60))
                store.set_meta("schema_version", 1)

            with WorldStore(path) as upgraded:
                self.assertEqual(upgraded.get_meta("schema_version"), "4")
                self.assertEqual(upgraded.get_agent("a").display_name, "A")
                self.assertEqual(upgraded.list_invitations(), [])
                self.assertEqual(upgraded.list_commitments(), [])
                self.assertEqual(upgraded.list_story_arcs(), [])

    def test_nested_transaction_rolls_back_all_world_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                store.add_agent(AgentState("a", "A", "home", 10, 0.2, "ok", 60))
                original = store.get_agent("a")
                assert original is not None
                with self.assertRaises(RuntimeError):
                    with store.transaction():
                        original.money = 99
                        store.update_agent(original)
                        store.append_event(1, "should_rollback", "a")
                        raise RuntimeError("force rollback")

                restored = store.get_agent("a")
                assert restored is not None
                self.assertEqual(restored.money, 10)
                self.assertEqual(store.list_events(), [])

    def test_memory_cognition_fields_are_persistent_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(path) as store:
                store.add_agent(AgentState("a", "A", "home", 10, 0.2, "ok", 60))
                event_id = store.append_event(10, "fact", "a")
                first = store.append_memory(
                    "a", event_id, 0.8, "A saw the broken sign.", ["unresolved"],
                    source_type="direct", confidence=0.95,
                    visibility="private", canonical_key="sign:broken",
                )
                duplicate = store.append_memory(
                    "a", event_id, 0.8, "A saw the broken sign.", ["unresolved"],
                    source_type="direct", confidence=0.95,
                    visibility="private", canonical_key="sign:broken",
                )
                self.assertEqual(first, duplicate)
                self.assertEqual(store.list_memories("a")[0]["source_type"], "direct")
            with WorldStore(path) as reopened:
                memory = reopened.list_memories("a")[0]
                self.assertEqual(memory["canonical_key"], "sign:broken")
                self.assertEqual(memory["confidence"], 0.95)
                self.assertEqual(reopened.retrieve_memories("a", 20)[0].source_type, "direct")

    def test_legacy_schema_memory_columns_migrate_with_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE world_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE agents (agent_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, location TEXT NOT NULL, money INTEGER NOT NULL, fatigue REAL NOT NULL, mood TEXT NOT NULL, daily_wake_minute INTEGER NOT NULL);
                CREATE TABLE events (event_id INTEGER PRIMARY KEY, tick INTEGER NOT NULL, event_type TEXT NOT NULL, actor_id TEXT, target_id TEXT, payload_json TEXT NOT NULL);
                CREATE TABLE memories (memory_id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, event_id INTEGER NOT NULL, importance REAL NOT NULL, summary TEXT NOT NULL, tags_json TEXT NOT NULL);
                INSERT INTO world_meta VALUES ('schema_version', '3');
                INSERT INTO agents VALUES ('a', 'A', 'home', 1, 0.1, 'ok', 60);
                INSERT INTO events VALUES (1, 10, 'fact', 'a', NULL, '{}');
                INSERT INTO memories VALUES (1, 'a', 1, 0.4, 'old fact', '["legacy"]');
                """
            )
            connection.commit()
            connection.close()
            with WorldStore(path) as store:
                memory = store.list_memories("a")[0]
                self.assertEqual(store.get_meta("schema_version"), "4")
                self.assertEqual(memory["source_type"], "legacy")
                self.assertEqual(memory["confidence"], 0.5)
                self.assertEqual(memory["archived"], False)

    def test_memory_compaction_preserves_important_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                store.add_agent(AgentState("a", "A", "home", 10, 0.2, "ok", 60))
                for tick in range(5):
                    event_id = store.append_event(tick, "chat", "a")
                    store.append_memory("a", event_id, 0.2, f"small-{tick}", ["chat"])
                important_event = store.append_event(20, "promise", "a")
                store.append_memory("a", important_event, 0.9, "Keep this promise.", ["promise"])
                result = store.compact_memories("a", max_active=2)
                self.assertGreater(result["archived_count"], 0)
                active = store.retrieve_memories("a", 30, limit=20)
                self.assertTrue(any(item.summary == "Keep this promise." for item in active))

    def test_memory_growth_triggers_compaction_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(path) as store:
                store.add_agent(AgentState("a", "A", "home", 10, 0.2, "ok", 60))
                protected_event = store.append_event(0, "promise", "a")
                store.append_memory(
                    "a",
                    protected_event,
                    0.9,
                    "A still owes B an answer.",
                    ["promise", "unresolved"],
                )
                for tick in range(261):
                    event_id = store.append_event(tick + 1, "chat", "a")
                    store.append_memory(
                        "a", event_id, 0.1, f"routine chat {tick}", ["chat"]
                    )
                active_before = [
                    item for item in store.list_memories("a") if not item["archived"]
                ]
                self.assertLessEqual(len(active_before), 260)
                self.assertTrue(
                    any(item["summary"] == "A still owes B an answer." for item in active_before)
                )
            with WorldStore(path) as reopened:
                active_after = [
                    item for item in reopened.list_memories("a") if not item["archived"]
                ]
                self.assertEqual(active_after, active_before)

    def test_database_enforces_active_canonical_memory_uniqueness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                store.add_agent(AgentState("a", "A", "home", 10, 0.2, "ok", 60))
                event_id = store.append_event(1, "fact", "a")
                store.append_memory(
                    "a", event_id, 0.5, "one", ["fact"], canonical_key="same"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    store._conn.execute(
                        """
                        INSERT INTO memories(
                            agent_id, event_id, importance, summary, tags_json,
                            source_type, confidence, visibility, archived, canonical_key
                        ) VALUES('a', ?, 0.5, 'two', '[]', 'direct', 0.8,
                                 'private', 0, 'same')
                        """,
                        (event_id,),
                    )


if __name__ == "__main__":
    unittest.main()
