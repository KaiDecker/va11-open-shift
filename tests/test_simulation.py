from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from open_shift.models import ActionProposal, ActionType, DAY_MINUTES
from open_shift.providers import MockProvider
from open_shift.rules import BAR_VISIT_COST, WORK_WAGE, RuleEngine
from open_shift.scenario import create_demo_world
from open_shift.store import WorldStore


def _event_signature(store: WorldStore) -> list[tuple[object, ...]]:
    return [
        (
            event["tick"],
            event["event_type"],
            event["actor_id"],
            event["target_id"],
            event["payload"],
        )
        for event in store.list_events()
    ]


class SimulationTests(unittest.TestCase):
    def test_thirty_day_soak_preserves_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                engine = create_demo_world(store, MockProvider(), seed=7)
                report = engine.run_days(30)

                self.assertEqual(report.current_tick, 30 * DAY_MINUTES)
                self.assertGreaterEqual(report.processed_turns, 300)
                self.assertEqual(report.rejected_actions, 0)
                self.assertEqual(report.provider_errors, 0)
                self.assertGreater(report.memory_count, 300)
                self.assertGreaterEqual(len(report.completed_goals), 3)
                self.assertIn("visited_bar", report.event_counts)
                self.assertIn("message_sent", report.event_counts)

                valid_locations = {"home", "work", "va11_hall_a"}
                for agent in store.list_agents():
                    self.assertIn(agent.location, valid_locations)
                    self.assertGreaterEqual(agent.money, 0)
                    self.assertGreaterEqual(agent.fatigue, 0)
                    self.assertLessEqual(agent.fatigue, 1)

                ticks = [event["tick"] for event in store.list_events()]
                self.assertEqual(ticks, sorted(ticks))
                for relationship in store.list_relationships():
                    self.assertGreaterEqual(relationship.trust, -1)
                    self.assertLessEqual(relationship.trust, 1)
                    self.assertGreaterEqual(relationship.warmth, -1)
                    self.assertLessEqual(relationship.warmth, 1)

    def test_same_seed_produces_identical_world(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "first.sqlite3"
            second_path = Path(temp_dir) / "second.sqlite3"
            with WorldStore(first_path) as first:
                create_demo_world(first, MockProvider(), seed=11).run_days(30)
                first_state = first.dump_state()
                first_events = _event_signature(first)
            with WorldStore(second_path) as second:
                create_demo_world(second, MockProvider(), seed=11).run_days(30)
                second_state = second.dump_state()
                second_events = _event_signature(second)

            self.assertEqual(first_state, second_state)
            self.assertEqual(first_events, second_events)

    def test_resume_matches_continuous_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            continuous_path = Path(temp_dir) / "continuous.sqlite3"
            resumed_path = Path(temp_dir) / "resumed.sqlite3"

            with WorldStore(continuous_path) as store:
                create_demo_world(store, MockProvider(), seed=13).run_days(30)
                continuous_state = store.dump_state()
                continuous_events = _event_signature(store)

            with WorldStore(resumed_path) as store:
                create_demo_world(store, MockProvider(), seed=13).run_days(15)
            with WorldStore(resumed_path) as store:
                create_demo_world(store, MockProvider(), seed=13).run_days(15)
                resumed_state = store.dump_state()
                resumed_events = _event_signature(store)

            self.assertEqual(continuous_state, resumed_state)
            self.assertEqual(continuous_events, resumed_events)

    def test_rule_engine_rejects_overspending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                create_demo_world(store, MockProvider(), seed=7)
                dorothy = store.get_agent("dorothy")
                assert dorothy is not None
                dorothy.money = BAR_VISIT_COST - 1
                store.update_agent(dorothy)
                rules = RuleEngine(store, ("home", "work", "va11_hall_a"))
                result = rules.execute(
                    1,
                    "dorothy",
                    ActionProposal(
                        ActionType.VISIT_BAR,
                        target_id="alma",
                        amount=1,
                    ),
                )
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, "insufficient_funds")
                unchanged = store.get_agent("dorothy")
                assert unchanged is not None
                self.assertEqual(unchanged.money, dorothy.money)

    def test_provider_cannot_choose_wage_or_bar_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                create_demo_world(store, MockProvider(), seed=7)
                rules = RuleEngine(store, ("home", "work", "va11_hall_a"))
                before = store.get_agent("dorothy")
                assert before is not None
                worked = rules.execute(
                    1,
                    "dorothy",
                    ActionProposal(
                        ActionType.WORK,
                        metadata={"wage": 1_000_000_000},
                    ),
                )
                self.assertTrue(worked.accepted)
                after_work = store.get_agent("dorothy")
                assert after_work is not None
                self.assertEqual(after_work.money, before.money + WORK_WAGE)

                visited = rules.execute(
                    2,
                    "dorothy",
                    ActionProposal(
                        ActionType.VISIT_BAR,
                        target_id="alma",
                        amount=1,
                    ),
                )
                self.assertTrue(visited.accepted)
                after_visit = store.get_agent("dorothy")
                assert after_visit is not None
                self.assertEqual(
                    after_visit.money,
                    before.money + WORK_WAGE - BAR_VISIT_COST,
                )


if __name__ == "__main__":
    unittest.main()
