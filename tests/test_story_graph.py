from __future__ import annotations

import copy
import tempfile
import time
import unittest
from pathlib import Path

from open_shift.dialogue import (
    DialogueLineDraft,
    DialogueTurnContext,
    PlayerDialogueTurnContext,
)
from open_shift.bridge import BridgeError
from open_shift.drinks import DRINK_RECIPES, SERVICE_INCOME, ServiceCategory
from open_shift.models import DecisionContext
from open_shift.providers import MockProvider
from open_shift.scenario import create_demo_world
from open_shift.store import WorldStore
from open_shift.story_graph import (
    DAILY_STORY_GRAPH_VERSION,
    MAX_DAILY_CUSTOMERS,
    DailyStoryGraph,
    StoryNodeKind,
)
from open_shift.world_bridge import WorldSceneService


class RecordingProvider:
    def __init__(self) -> None:
        self.policy = MockProvider()
        self.dialogue_calls = 0
        self.player_calls = 0

    def decide(self, context: DecisionContext):
        return self.policy.decide(context)

    def generate_dialogue_line(
        self, context: DialogueTurnContext
    ) -> DialogueLineDraft:
        self.dialogue_calls += 1
        return DialogueLineDraft("neutral", "今晚吧台的动静听起来和平常不太一样。")

    def generate_player_dialogue_line(
        self, context: PlayerDialogueTurnContext
    ) -> DialogueLineDraft:
        self.player_calls += 1
        return DialogueLineDraft("neutral", "我听见了。先把眼前这杯处理好。")


class DailyStoryGraphTests(unittest.TestCase):
    @staticmethod
    def _wait_for_status(db_path: Path, day_index: int, status: str) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with WorldStore(db_path) as store:
                record = store.get_daily_story_graph(
                    day_index, DAILY_STORY_GRAPH_VERSION
                )
                if record is not None and record["status"] == status:
                    return
            time.sleep(0.01)
        raise AssertionError(f"day {day_index} did not reach {status}")

    @staticmethod
    def _prepare(db_path: Path) -> DailyStoryGraph:
        return WorldSceneService(
            db_path,
            provider_factory=MockProvider,
            advance_minutes=0,
        ).prepare_daily_story_graph(1)

    @staticmethod
    def _ack(scene_id: str, request_id: str, outcome: str) -> dict[str, object]:
        return {
            "protocol_version": 1,
            "request_id": request_id,
            "client_session_id": "story-session-0001",
            "scene_id": scene_id,
            "outcome": outcome,
        }

    @staticmethod
    def _exact_drink(drink_id: str) -> dict[str, object]:
        recipe = next(item for item in DRINK_RECIPES if item.drink_id == drink_id)
        ingredients = list(recipe.ingredients)
        if recipe.optional_karmotrine:
            ingredients[4] = 1
        return {
            "adelhyde": ingredients[0],
            "bronson_extract": ingredients[1],
            "powdered_delta": ingredients[2],
            "flanergide": ingredients[3],
            "karmotrine": ingredients[4],
            "ice": int(recipe.ice),
            "aged": int(recipe.aged),
            "preparation": recipe.preparation,
        }

    def test_graph_is_bounded_has_four_branches_and_strict_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            graph = self._prepare(Path(temp_dir) / "world.sqlite3")

        self.assertEqual(DailyStoryGraph.from_dict(graph.to_dict()), graph)
        arrivals = [
            node for node in graph.nodes if node.kind is StoryNodeKind.ARRIVAL_ORDER
        ]
        self.assertGreaterEqual(len(arrivals), 1)
        self.assertLessEqual(len(arrivals), MAX_DAILY_CUSTOMERS)
        self.assertEqual(len(graph.nodes), len(arrivals) * 6)
        for arrival in arrivals:
            self.assertEqual(
                set(dict(arrival.branch_targets)),
                {category.value for category in ServiceCategory},
            )

        malformed = copy.deepcopy(graph.to_dict())
        malformed["unexpected"] = True
        with self.assertRaises(ValueError):
            DailyStoryGraph.from_dict(malformed)

        duplicate_branch = copy.deepcopy(graph.to_dict())
        first_arrival = next(
            node
            for node in duplicate_branch["nodes"]
            if node["kind"] == StoryNodeKind.ARRIVAL_ORDER.value
        )
        first_arrival["branch_targets"]["exact"] = first_arrival[
            "branch_targets"
        ]["wrong"]
        with self.assertRaises(ValueError):
            DailyStoryGraph.from_dict(duplicate_branch)

        if len(arrivals) > 1:
            cyclic = copy.deepcopy(graph.to_dict())
            first_merge = next(
                node
                for node in cyclic["nodes"]
                if node["node_id"] == arrivals[0].branch_targets[0][1].replace(
                    "_exact", "_merge"
                )
            )
            first_merge["next_node_id"] = arrivals[0].node_id
            with self.assertRaisesRegex(ValueError, "cycle"):
                DailyStoryGraph.from_dict(cyclic)

    def test_ready_graph_replays_without_constructing_a_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            provider = RecordingProvider()
            graph = WorldSceneService(
                db_path,
                provider_factory=lambda: provider,
                advance_minutes=0,
            ).prepare_daily_story_graph(1)
            self.assertGreater(provider.dialogue_calls, 0)
            self.assertGreater(provider.player_calls, 0)

            def forbidden_factory():
                raise AssertionError("ready replay constructed a provider")

            replay = WorldSceneService(
                db_path,
                provider_factory=forbidden_factory,
                advance_minutes=0,
            ).prepare_daily_story_graph(1)
            self.assertEqual(replay, graph)

    def test_failed_generation_reuses_sources_and_records_only_safe_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"

            def failing_factory():
                raise RuntimeError("secret provider detail")

            with self.assertRaises(RuntimeError):
                WorldSceneService(
                    db_path,
                    provider_factory=failing_factory,
                    advance_minutes=0,
                ).prepare_daily_story_graph(1)
            with WorldStore(db_path) as store:
                failed = store.get_daily_story_graph(
                    1, DAILY_STORY_GRAPH_VERSION
                )
                self.assertIsNotNone(failed)
                assert failed is not None
                source_ids = failed["source_event_ids"]
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["error_code"], "RuntimeError")
                self.assertNotIn("secret", repr(failed))
                self.assertEqual(failed["attempt_count"], 1)

            graph = self._prepare(db_path)
            self.assertEqual(graph.source_event_ids, source_ids)
            with WorldStore(db_path) as store:
                ready = store.get_daily_story_graph(1, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(ready)
                assert ready is not None
                self.assertEqual(ready["status"], "ready")
                self.assertEqual(ready["attempt_count"], 2)
                self.assertIsNone(ready["error_code"])

    def test_interrupted_generation_is_retried_with_the_same_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(db_path) as store:
                create_demo_world(store, MockProvider(), seed=7)
                events = WorldSceneService._daily_source_events(store.list_events())
                source_ids = tuple(event["event_id"] for event in events)
                store.begin_daily_story_graph(
                    1,
                    DAILY_STORY_GRAPH_VERSION,
                    store.current_tick,
                    source_ids,
                )

            graph = self._prepare(db_path)
            self.assertEqual(graph.source_event_ids, source_ids)
            with WorldStore(db_path) as store:
                record = store.get_daily_story_graph(1, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record["status"], "ready")
                self.assertEqual(record["attempt_count"], 2)

    def test_candidate_generation_has_no_authoritative_world_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(db_path) as store:
                create_demo_world(store, MockProvider(), seed=7)
                before = {
                    "events": store.list_events(),
                    "memories": {
                        agent.agent_id: store.list_memories(agent.agent_id)
                        for agent in store.list_agents()
                    },
                    "relationships": store.list_relationships(),
                    "goals": store.list_goals(),
                    "money": {
                        agent.agent_id: agent.money for agent in store.list_agents()
                    },
                }

            self._prepare(db_path)
            with WorldStore(db_path) as store:
                self.assertEqual(store.list_events(), before["events"])
                self.assertEqual(store.list_relationships(), before["relationships"])
                self.assertEqual(store.list_goals(), before["goals"])
                self.assertEqual(
                    {agent.agent_id: agent.money for agent in store.list_agents()},
                    before["money"],
                )
                self.assertEqual(
                    {
                        agent.agent_id: store.list_memories(agent.agent_id)
                        for agent in store.list_agents()
                    },
                    before["memories"],
                )
                self.assertFalse(
                    any(
                        event["event_type"] in {"drink_served", "player_scene_ack"}
                        for event in store.list_events()
                    )
                )

    def test_only_the_served_result_branch_is_committed_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            provider = RecordingProvider()
            service = WorldSceneService(
                db_path,
                provider_factory=lambda: provider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            service.prepare_daily_story_graph(1)
            doorbell = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "story-open-doorbell",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertTrue(all(line.speaker_id is None for line in doorbell.lines))
            service.ack_scene(
                self._ack(
                    doorbell.scene_id,
                    "story-ack-doorbell",
                    "continued_in_bar",
                )
            )
            arrival = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "story-open-1",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertIsNotNone(arrival.order)
            assert arrival.order is not None
            service.ack_scene(
                self._ack(arrival.scene_id, "story-ack-1", "order_started")
            )
            resolve_request = {
                "protocol_version": 1,
                "request_id": "story-order-1",
                "client_session_id": "story-session-0001",
                "scene_id": arrival.scene_id,
                "order_id": arrival.order.order_id,
                "drink": self._exact_drink(arrival.order.requested_drink_id),
            }
            resolution = service.resolve_order(resolve_request)
            self.assertEqual(resolution.result.category, ServiceCategory.EXACT)
            self.assertEqual(resolution.income_delta, SERVICE_INCOME[ServiceCategory.EXACT])
            self.assertEqual(service.resolve_order(resolve_request), resolution)

            with WorldStore(db_path) as store:
                commits = store.list_story_branch_commits()
                self.assertEqual(len(commits), 1)
                self.assertEqual(commits[0]["category"], "exact")
                self.assertEqual(
                    store.get_meta("player_shift_income"),
                    str(SERVICE_INCOME[ServiceCategory.EXACT]),
                )
                progress = store.get_daily_story_progress(
                    1, DAILY_STORY_GRAPH_VERSION
                )
                self.assertIsNotNone(progress)
                assert progress is not None
                self.assertEqual(progress["current_node_id"], commits[0]["result_node_id"])
                committed_scene_ids = {
                    event["payload"].get("scene_id")
                    for event in store.list_events()
                    if event["event_type"] == "agent_dialogue_completed"
                }
                self.assertNotIn(resolution.scene.scene_id, committed_scene_ids)

            service.ack_scene(
                self._ack(
                    resolution.scene.scene_id,
                    "story-ack-2",
                    "continued_in_bar",
                )
            )
            next_arrival = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "story-open-2",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertIsNotNone(next_arrival.order)
            self.assertNotEqual(next_arrival.scene_id, arrival.scene_id)
            with WorldStore(db_path) as store:
                selected_memories = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "agent_dialogue_completed"
                    and event["payload"].get("scene_id") == resolution.scene.scene_id
                ]
                self.assertEqual(len(selected_memories), 1)
                graph_record = store.get_daily_story_graph(
                    1, DAILY_STORY_GRAPH_VERSION
                )
                assert graph_record is not None
                graph = DailyStoryGraph.from_dict(graph_record["graph"])
                unselected = {
                    node.scene.scene_id
                    for node in graph.nodes
                    if node.kind is StoryNodeKind.RESULT_DIALOGUE
                    and node.scene is not None
                    and node.scene.scene_id != resolution.scene.scene_id
                }
                remembered = {
                    event["payload"].get("scene_id")
                    for event in store.list_events()
                    if event["event_type"] == "agent_dialogue_completed"
                }
                self.assertTrue(unselected.isdisjoint(remembered))
            service.wait_for_background_generation()

    def test_sqlite_reopen_restores_valid_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            expected = self._prepare(db_path)
            with WorldStore(db_path) as store:
                record = store.get_daily_story_graph(
                    1, DAILY_STORY_GRAPH_VERSION
                )
                self.assertIsNotNone(record)
                assert record is not None
                restored = DailyStoryGraph.from_dict(record["graph"])
                self.assertEqual(restored, expected)
                self.assertEqual(store.list_daily_story_graphs(), [record])

    def test_first_entry_uses_ambient_text_and_prefetches_only_one_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            opening = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "ambient-open-1",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertTrue(opening.scene_id.startswith("opening_"))
            self.assertTrue(all(line.speaker_id is None for line in opening.lines))
            persisted_opening = opening.to_dict()
            transported_opening = opening.to_gamemaker_dict()
            self.assertIsNone(persisted_opening["lines"][0]["speaker_id"])
            self.assertIsNone(persisted_opening["lines"][0]["portrait_id"])
            self.assertEqual(transported_opening["lines"][0]["speaker_id"], "")
            self.assertEqual(transported_opening["lines"][0]["portrait_id"], "")
            service.ack_scene(
                self._ack(opening.scene_id, "ambient-ack-1", "continued_in_bar")
            )
            self._wait_for_status(db_path, 1, "ready")

            doorbell = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "ambient-open-2",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertEqual(doorbell.lines[0].text, "门铃响了。")
            self.assertIsNone(doorbell.lines[0].speaker_id)
            service.ack_scene(
                self._ack(doorbell.scene_id, "ambient-ack-2", "continued_in_bar")
            )
            self._wait_for_status(db_path, 2, "ready")
            with WorldStore(db_path) as store:
                self.assertEqual(
                    [item["day_index"] for item in store.list_daily_story_graphs()],
                    [1, 2],
                )
                self.assertFalse(
                    any(
                        event["event_type"] == "agent_dialogue_completed"
                        and event["payload"].get("scene_id")
                        in {opening.scene_id, doorbell.scene_id}
                        for event in store.list_events()
                    )
                )
            service.wait_for_background_generation()

    def test_generation_failure_is_reported_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"

            def failing_factory():
                raise RuntimeError("private diagnostic")

            service = WorldSceneService(
                db_path,
                provider_factory=failing_factory,
                advance_minutes=0,
                daily_story_mode=True,
            )
            opening = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "failure-open-1",
                    "client_session_id": "story-session-0001",
                }
            )
            service.ack_scene(
                self._ack(opening.scene_id, "failure-ack-1", "continued_in_bar")
            )
            self._wait_for_status(db_path, 1, "failed")
            with self.assertRaises(BridgeError) as raised:
                service.open_scene(
                    {
                        "protocol_version": 1,
                        "request_id": "failure-open-2",
                        "client_session_id": "story-session-0001",
                    }
                )
            self.assertEqual(raised.exception.code, "story_generation_failed")
            self.assertIn("RuntimeError", raised.exception.message)
            self.assertNotIn("private diagnostic", raised.exception.message)

            waiting = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "failure-open-3",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertTrue(waiting.scene_id.startswith("waiting_"))
            self.assertIsNone(waiting.lines[0].speaker_id)
            service.wait_for_background_generation()


if __name__ == "__main__":
    unittest.main()
