from __future__ import annotations

import copy
import json
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
from open_shift.byok import BYOKTransportError
from open_shift.drinks import DRINK_RECIPES, ServiceCategory
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

    def test_on_demand_mode_generates_only_reached_scene_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            provider = RecordingProvider()
            service = WorldSceneService(
                db_path,
                provider_factory=lambda: provider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            service.prepare_story_day({"request_id": "on-demand-prepare"})
            self.assertEqual((provider.dialogue_calls, provider.player_calls), (0, 0))

            opening = service.open_scene(
                {"protocol_version": 1, "request_id": "on-demand-open-1", "client_session_id": "story-session-0001"}
            )
            service.ack_scene(self._ack(opening.scene_id, "on-demand-ack-1", "continued_in_bar"))
            doorbell = service.open_scene(
                {"protocol_version": 1, "request_id": "on-demand-open-2", "client_session_id": "story-session-0001"}
            )
            service.ack_scene(self._ack(doorbell.scene_id, "on-demand-ack-2", "continued_in_bar"))
            self.assertEqual((provider.dialogue_calls, provider.player_calls), (0, 0))

            arrival = service.open_scene(
                {"protocol_version": 1, "request_id": "on-demand-open-3", "client_session_id": "story-session-0001"}
            )
            self.assertEqual((provider.dialogue_calls, provider.player_calls), (1, 1))
            assert arrival.order is not None
            service.ack_scene(self._ack(arrival.scene_id, "on-demand-ack-3", "order_started"))
            service.resolve_order(
                {
                    "protocol_version": 1,
                    "request_id": "on-demand-order-1",
                    "client_session_id": "story-session-0001",
                    "scene_id": arrival.scene_id,
                    "order_id": arrival.order.order_id,
                    "drink": self._exact_drink(arrival.order.requested_drink_id),
                }
            )
            self.assertEqual((provider.dialogue_calls, provider.player_calls), (2, 2))

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

    def test_byok_failure_falls_back_to_a_playable_local_graph(self) -> None:
        class FailingBYOKProvider:
            @staticmethod
            def decide(context):
                return MockProvider().decide(context)

            @staticmethod
            def generate_dialogue_line(context):
                raise BYOKTransportError("private transport detail")

            @staticmethod
            def generate_player_dialogue_line(context):
                raise BYOKTransportError("private transport detail")

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            graph = WorldSceneService(
                db_path,
                provider_factory=FailingBYOKProvider,
                advance_minutes=0,
                daily_story_mode=True,
            ).prepare_daily_story_graph(1)
            self.assertEqual(graph.day_index, 1)
            with WorldStore(db_path) as store:
                record = store.get_daily_story_graph(1, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record["status"], "ready")
                self.assertIsNone(record["error_code"])

    def test_required_provider_failure_does_not_use_local_dialogue(self) -> None:
        class FailingBYOKProvider:
            @staticmethod
            def decide(context):
                return MockProvider().decide(context)

            @staticmethod
            def generate_dialogue_line(context):
                raise BYOKTransportError("private transport detail")

            @staticmethod
            def generate_player_dialogue_line(context):
                raise BYOKTransportError("private transport detail")

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=FailingBYOKProvider,
                advance_minutes=0,
                daily_story_mode=True,
                allow_provider_fallback=False,
            )
            with self.assertRaises(BYOKTransportError):
                service.prepare_daily_story_graph(1)
            with WorldStore(db_path) as store:
                record = store.get_daily_story_graph(1, DAILY_STORY_GRAPH_VERSION)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["error_code"], "BYOKTransportError")

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
            opening = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "story-open-doorbell",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertEqual(opening.scene_id, "opening_day_1")
            service.ack_scene(
                self._ack(
                    opening.scene_id,
                    "story-ack-opening",
                    "continued_in_bar",
                )
            )
            doorbell = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "story-open-real-doorbell",
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
            requested_recipe = next(
                recipe
                for recipe in DRINK_RECIPES
                if recipe.drink_id == arrival.order.requested_drink_id
            )
            self.assertEqual(resolution.income_delta, requested_recipe.price)
            self.assertEqual(service.resolve_order(resolve_request), resolution)

            with WorldStore(db_path) as store:
                commits = store.list_story_branch_commits()
                self.assertEqual(len(commits), 1)
                self.assertEqual(commits[0]["category"], "exact")
                self.assertEqual(
                    store.get_meta("player_shift_income"),
                    str(requested_recipe.price),
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
            # Entry builds only the local skeleton; no next-day graph or
            # provider dialogue is prefetched.
            with WorldStore(db_path) as store:
                record = store.get_daily_story_graph(1, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record["status"], "ready")

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
            with WorldStore(db_path) as store:
                self.assertEqual(
                    [item["day_index"] for item in store.list_daily_story_graphs()],
                    [1],
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
            # The first actual scene request is the first point at which the
            # provider is touched; failure is surfaced by that request.
            doorbell = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "failure-open-2",
                    "client_session_id": "story-session-0001",
                }
            )
            service.ack_scene(
                self._ack(doorbell.scene_id, "failure-ack-2", "continued_in_bar")
            )
            with self.assertRaises(RuntimeError):
                service.open_scene(
                    {
                        "protocol_version": 1,
                        "request_id": "failure-open-3",
                        "client_session_id": "story-session-0001",
                    }
                )
            with WorldStore(db_path) as store:
                record = store.get_daily_story_graph(1, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record["status"], "ready")

    def test_completed_shift_opens_next_day_without_requiring_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            graph = service.prepare_daily_story_graph(1)
            request_index = 1

            def opened():
                nonlocal request_index
                scene = service.open_scene(
                    {
                        "protocol_version": 1,
                        "request_id": f"day-loop-open-{request_index}",
                        "client_session_id": "day-loop-session-0001",
                    }
                )
                request_index += 1
                return scene

            opening = opened()
            self.assertEqual(opening.scene_id, "opening_day_1")
            service.ack_scene(
                self._ack(opening.scene_id, "day-loop-ack-opening", "continued_in_bar")
            )
            doorbell = opened()
            service.ack_scene(
                self._ack(doorbell.scene_id, "day-loop-ack-doorbell", "continued_in_bar")
            )

            arrivals = [
                node for node in graph.nodes if node.kind is StoryNodeKind.ARRIVAL_ORDER
            ]
            expected_income = 0
            for index, _ in enumerate(arrivals, start=1):
                arrival = opened()
                assert arrival.order is not None
                service.ack_scene(
                    self._ack(
                        arrival.scene_id,
                        f"day-loop-ack-arrival-{index}",
                        "order_started",
                    )
                )
                resolution = service.resolve_order(
                    {
                        "protocol_version": 1,
                        "request_id": f"day-loop-order-{index}",
                        "client_session_id": "day-loop-session-0001",
                        "scene_id": arrival.scene_id,
                        "order_id": arrival.order.order_id,
                        "drink": self._exact_drink(arrival.order.requested_drink_id),
                    }
                )
                expected_income += resolution.income_delta
                service.ack_scene(
                    self._ack(
                        resolution.scene.scene_id,
                        f"day-loop-ack-result-{index}",
                        "continued_in_bar",
                    )
                )

            closing = opened()
            self.assertEqual(closing.scene_id, "closing_day_1")
            service.ack_scene(
                self._ack(closing.scene_id, "day-loop-ack-closing", "continued_in_bar")
            )
            settlement = opened()
            self.assertEqual(settlement.scene_id, "settlement_day_1")
            self.assertIn(f"¥{expected_income}", settlement.lines[0].text)
            service.ack_scene(
                self._ack(
                    settlement.scene_id,
                    "day-loop-ack-settlement",
                    "continued_in_bar",
                )
            )
            next_opening = opened()
            self.assertEqual(next_opening.scene_id, "opening_day_2")
            with WorldStore(db_path) as store:
                self.assertEqual(store.get_meta("current_story_day"), "2")
                self.assertEqual(store.get_meta("shift_phase"), "playing")
                self.assertEqual(store.get_meta("player_shift_income"), "0")
                self.assertEqual(
                    store.get_meta("player_shift_income_day_1"), str(expected_income)
                )
                completed = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "player_shift_completed"
                ]
                self.assertEqual(len(completed), 1)

            service.wait_for_background_generation()

    def test_apartment_prepare_recovers_lost_settlement_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            service.prepare_daily_story_graph(1)
            with WorldStore(db_path) as store:
                with store.transaction():
                    store._conn.execute(
                        "UPDATE daily_story_progress SET status = 'completed', "
                        "current_node_id = NULL "
                        "WHERE day_index = 1 AND generation_version = ?",
                        (DAILY_STORY_GRAPH_VERSION,),
                    )
                    store.set_meta("player_shift_income", "180")
            prepared = service.prepare_story_day({"request_id": "recover-prepare"})
            self.assertEqual(prepared["world_day"], 2)
            with WorldStore(db_path) as store:
                self.assertEqual(store.get_meta("current_story_day"), "2")
                self.assertEqual(store.get_meta("last_completed_story_day"), "1")
                self.assertEqual(store.get_meta("player_shift_income"), "0")
                self.assertEqual(store.get_meta("player_shift_income_day_1"), "180")
                self.assertEqual(
                    len(
                        [
                            event
                            for event in store.list_events()
                            if event["event_type"] == "player_shift_completed"
                        ]
                    ),
                    1,
                )

            # A late client acknowledgement for the recovered settlement is
            # an idempotent success, not a false day-mismatch failure.
            settlement = service._settlement_scene(1, 180)
            with WorldStore(db_path) as store:
                with store.transaction():
                    store.set_meta("bridge_scene:settlement_day_1", "ambient")
                    store.set_meta(
                        "bridge_scene_payload:settlement_day_1",
                        json.dumps(settlement.to_dict(), ensure_ascii=False),
                    )
            service.ack_scene(
                self._ack(
                    "settlement_day_1",
                    "recover-late-settlement-ack",
                    "continued_in_bar",
                )
            )

    def test_stage_ten_save_gate_is_released_when_story_is_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(db_path) as store:
                store.set_meta("current_story_day", 2)
                store.set_meta("last_completed_story_day", 1)
                store.set_meta("shift_phase", "save_required")
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            prepared = service.prepare_story_day(
                {
                    "protocol_version": 1,
                    "request_id": "legacy-gate-prepare",
                    "client_session_id": "legacy-gate-session",
                }
            )
            self.assertEqual(prepared["world_day"], 2)
            self.assertEqual(prepared["shift_phase"], "playing")
            with WorldStore(db_path) as store:
                self.assertEqual(store.get_meta("shift_phase"), "playing")


if __name__ == "__main__":
    unittest.main()
