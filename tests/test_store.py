from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
