from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from open_shift.byok import decision_observation
from open_shift.models import ActionProposal, ActionType, DAY_MINUTES
from open_shift.providers import MockProvider
from open_shift.rules import RuleEngine
from open_shift.scenario import create_demo_world
from open_shift.store import WorldStore


class SocialSimulationTests(unittest.TestCase):
    def test_demo_has_five_persistent_agents_and_independent_arcs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                create_demo_world(store, MockProvider(), seed=7)
                self.assertEqual(len(store.list_agents()), 5)
                self.assertEqual(len(store.list_story_arcs(status="active")), 5)
                self.assertTrue(
                    any(arc.owner_id == "stella" for arc in store.list_story_arcs())
                )
                self.assertTrue(
                    any(arc.owner_id == "sei" for arc in store.list_story_arcs())
                )

    def test_invitation_and_promise_are_persistent_future_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                create_demo_world(store, MockProvider(), seed=7)
                rules = RuleEngine(store, ("home", "work", "va11_hall_a"))
                invited = rules.execute(
                    100,
                    "dana",
                    ActionProposal(
                        ActionType.INVITE,
                        target_id="dorothy",
                        location="va11_hall_a",
                        duration_minutes=60,
                        reason_code="meet_friend",
                    ),
                )
                promised = rules.execute(
                    110,
                    "dana",
                    ActionProposal(
                        ActionType.PROMISE,
                        target_id="dorothy",
                        duration_minutes=60,
                        reason_code="help_friend",
                    ),
                )
                self.assertTrue(invited.accepted)
                self.assertTrue(promised.accepted)
                self.assertEqual(len(store.list_invitations(status="pending")), 1)
                self.assertEqual(len(store.list_commitments(status="pending")), 1)

                rules.resolve_invitation(160, 1)
                rules.resolve_commitment(170, 1)
                self.assertEqual(store.list_invitations()[0].status, "accepted")
                self.assertEqual(store.list_commitments()[0].status, "fulfilled")
                event_types = {event["event_type"] for event in store.list_events()}
                self.assertIn("invitation_kept", event_types)
                self.assertIn("promise_fulfilled", event_types)

    def test_memory_retrieval_is_private_relevant_bounded_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                create_demo_world(store, MockProvider(), seed=7)
                private_event = store.append_event(
                    10, "private_fact", "dana", payload={"secret": True}
                )
                store.append_memory(
                    "dana", private_event, 0.2, "Dana knows a private detail.", ["secret"]
                )
                social_event = store.append_event(20, "social_fact", "dana", "dorothy")
                store.append_memory(
                    "dana",
                    social_event,
                    0.5,
                    "Dorothy asked Dana for help.",
                    ["social", "dorothy"],
                )
                store.append_memory(
                    "dorothy",
                    social_event,
                    0.5,
                    "I asked Dana for help.",
                    ["social", "dana"],
                )

                first = store.retrieve_memories(
                    "dana", 100, tags={"dorothy"}, limit=2, character_budget=60
                )
                second = store.retrieve_memories(
                    "dana", 100, tags={"dorothy"}, limit=2, character_budget=60
                )
                self.assertEqual(first, second)
                self.assertEqual(first[0].event_id, social_event)
                self.assertLessEqual(sum(len(item.summary) for item in first), 60)
                self.assertTrue(all(item.summary != "I asked Dana for help." for item in first))

    def test_provider_observation_contains_only_actor_cognition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                engine = create_demo_world(store, MockProvider(), seed=7)
                dana_event = store.append_event(10, "private_fact", "dana")
                alma_event = store.append_event(11, "private_fact", "alma")
                store.append_memory("dana", dana_event, 0.8, "Dana secret", ["secret"])
                store.append_memory("alma", alma_event, 1.0, "Alma secret", ["secret"])
                observation = decision_observation(engine._context(20, "dana"))
                summaries = {
                    memory["summary"] for memory in observation["relevant_memories"]
                }
                self.assertIn("Dana secret", summaries)
                self.assertNotIn("Alma secret", summaries)

    def test_social_actions_advance_and_replace_story_arcs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                create_demo_world(store, MockProvider(), seed=7)
                rules = RuleEngine(store, ("home", "work", "va11_hall_a"))
                for tick in (100, 200, 300):
                    result = rules.execute(
                        tick,
                        "dana",
                        ActionProposal(
                            ActionType.MESSAGE,
                            target_id="dorothy",
                            reason_code="advance_arc",
                        ),
                    )
                    self.assertTrue(result.accepted)
                self.assertGreaterEqual(
                    len(store.list_story_arcs("dana", "resolved")), 1
                )
                self.assertGreaterEqual(
                    len(store.list_story_arcs("dana", "active")), 1
                )

    def test_social_state_resume_matches_continuous_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            continuous_path = Path(temp_dir) / "continuous.sqlite3"
            resumed_path = Path(temp_dir) / "resumed.sqlite3"
            with WorldStore(continuous_path) as store:
                create_demo_world(store, MockProvider(), seed=23).run_days(20)
                continuous = store.dump_state()
                continuous_events = store.list_events()
            with WorldStore(resumed_path) as store:
                create_demo_world(store, MockProvider(), seed=23).run_days(10)
            with WorldStore(resumed_path) as store:
                create_demo_world(store, MockProvider(), seed=23).run_days(10)
                resumed = store.dump_state()
                resumed_events = store.list_events()
            self.assertEqual(continuous, resumed)
            self.assertEqual(continuous_events, resumed_events)

    def test_one_hundred_day_unattended_social_soak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(db_path) as store:
                report = create_demo_world(store, MockProvider(), seed=19).run_days(100)
                self.assertEqual(report.current_tick, 100 * DAY_MINUTES)
                self.assertEqual(len(report.agents), 5)
                self.assertEqual(report.rejected_actions, 0)
                self.assertEqual(report.provider_errors, 0)
                self.assertGreater(report.resolved_story_arcs, 5)
                self.assertGreater(report.autonomous_event_ratio, 0.1)
                self.assertIn("invitation_kept", report.event_counts)
                self.assertIn("promise_fulfilled", report.event_counts)
                self.assertIn("goal_created", report.event_counts)
                self.assertLess(db_path.stat().st_size, 10 * 1024 * 1024)

                signatures = Counter(
                    (
                        event["event_type"],
                        event["actor_id"],
                        event["target_id"],
                        event["payload"].get("reason_code"),
                    )
                    for event in store.list_events()
                )
                total = sum(signatures.values())
                most_common = signatures.most_common(1)[0][1]
                self.assertLess(most_common / total, 0.2)

    def test_three_hundred_sixty_five_day_unattended_social_soak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(db_path) as store:
                report = create_demo_world(store, MockProvider(), seed=29).run_days(365)
                self.assertEqual(report.current_tick, 365 * DAY_MINUTES)
                self.assertEqual(report.rejected_actions, 0)
                self.assertEqual(report.provider_errors, 0)
                self.assertEqual(len(report.agents), 5)
                self.assertGreater(report.memory_count, 3000)
                for agent in store.list_agents():
                    self.assertGreaterEqual(agent.money, 0)
                    self.assertGreaterEqual(agent.fatigue, 0)
                    self.assertLessEqual(agent.fatigue, 1)
                self.assertLess(db_path.stat().st_size, 40 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
