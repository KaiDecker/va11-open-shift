from __future__ import annotations

import copy
import json
import re
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
from open_shift.drinks import AlcoholRequirement, DRINK_RECIPES, DrinkOrder, ServiceCategory, ServiceResult
from open_shift.models import DecisionContext, Memory, AgentState
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
from open_shift.world_events import CODE_OWNED_DAY_ONE_EVENTS, PublicWorldEvent


class RecordingProvider:
    def __init__(self) -> None:
        self.policy = MockProvider()
        self.dialogue_calls = 0
        self.player_calls = 0
        self.dialogue_contexts: list[DialogueTurnContext] = []
        self.player_contexts: list[PlayerDialogueTurnContext] = []

    def decide(self, context: DecisionContext):
        return self.policy.decide(context)

    def generate_dialogue_line(
        self, context: DialogueTurnContext
    ) -> DialogueLineDraft:
        self.dialogue_calls += 1
        self.dialogue_contexts.append(context)
        return DialogueLineDraft("neutral", f"{self._anchor(context)}这件事今晚得说清楚。")

    def generate_player_dialogue_line(
        self, context: PlayerDialogueTurnContext
    ) -> DialogueLineDraft:
        self.player_calls += 1
        self.player_contexts.append(context)
        return DialogueLineDraft("neutral", f"{self._anchor(context)}？你接着说。")

    @staticmethod
    def _anchor(context: DialogueTurnContext | PlayerDialogueTurnContext) -> str:
        direction = context.scene_direction
        topic = direction.event_topic if direction is not None else ""
        match = re.search(r"[\u4e00-\u9fff]{2,}", topic)
        return match.group(0)[:12] if match else "今晚"


class DailyStoryGraphTests(unittest.TestCase):
    def test_dialogue_memory_hint_preserves_private_source_and_budget(self) -> None:
        context = DecisionContext(
            tick=1800,
            seed=7,
            actor=AgentState("alma", "Alma", "va11_hall_a", 100, 0.1, "steady", 480),
            agents=(),
            relationships=(),
            goals=(),
            locations=(),
            memories=(
                Memory(1, 1, 1200, 0.9, "她答应明早再确认路线。", ("route",), "direct", 0.95, "private"),
                Memory(2, 2, 1300, 0.7, "有人提到诊所延长了夜间接诊。", ("clinic",), "heard", 0.8, "participants"),
            ),
        )
        hint = WorldSceneService._dialogue_memory_hint(context)
        self.assertIn("我亲自经历过：她答应明早再确认路线。", hint)
        self.assertIn("我听人提过：有人提到诊所延长了夜间接诊。", hint)
        self.assertLessEqual(len(hint), 360)

    def test_selected_public_event_is_joined_to_character_dialogue_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(path, advance_minutes=0)
            service.publish_public_world_event(
                PublicWorldEvent(
                    "day_one_public_transit",
                    "city",
                    "active",
                    "夜班电车临时改道",
                    "施工让末班车绕开旧城区。",
                    ("alma", "sei"),
                )
            )
            graph = service.prepare_daily_story_skeleton(1)
            topics = [
                node.topic
                for node in graph.nodes
                if node.kind is StoryNodeKind.ARRIVAL_ORDER
            ]
            self.assertTrue(topics)
            self.assertTrue(any("夜班电车临时改道" in topic for topic in topics))

    def test_dialogue_transcript_receipt_is_idempotent_without_ack_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(path, advance_minutes=0)
            lines = [{"line_id": "line_1", "speaker_id": "alma", "text": "听说电车改道了。"}]
            with WorldStore(path) as store, store.transaction():
                service._persist_dialogue_transcript(
                    store, story_day=1, scene_id="day_1_customer_1_order", lines=lines
                )
                service._persist_dialogue_transcript(
                    store, story_day=1, scene_id="day_1_customer_1_order", lines=lines
                )
                records = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "dialogue_transcript"
                ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["payload"]["lines"], lines)

    def test_day_one_narrative_perspectives_are_personal_and_actionable(self) -> None:
        names = {"alma": "Alma", "stella": "Stella", "dorothy": "Dorothy"}
        events = [
            item.to_dict() | {"event_id": index}
            for index, item in enumerate(CODE_OWNED_DAY_ONE_EVENTS, start=1)
        ]
        perspectives = [
            WorldSceneService._perspective_for_event(event, names, 0)
            for event in events
        ]
        topics = [item.event_topic for item in perspectives]
        self.assertEqual(len(set(topics)), 3)
        for topic, perspective in zip(topics, perspectives):
            self.assertTrue(perspective.anchor in topic)
            self.assertRegex(perspective.unresolved_question, r"还是|或")
            for stock in ("外面都在谈", "气象台", "预计", "街区商户", "施工封闭"):
                self.assertNotIn(stock, topic)

    def test_fallback_arrival_uses_customer_perspective(self) -> None:
        event = CODE_OWNED_DAY_ONE_EVENTS[0].to_dict() | {"event_id": 1}
        scene = WorldSceneService._fallback_scene(
            event, {"alma": "Alma", "stella": "Stella"}, 0
        )
        self.assertIn("路线", scene.lines[0].text)
        self.assertNotIn("外面都在谈", scene.lines[0].text)

    def test_event_premise_does_not_leak_unknown_internal_event_type(self) -> None:
        premise = WorldSceneService._event_premise(
            {
                "event_id": 99,
                "event_type": "story_arc_started",
                "actor_id": "alma",
                "target_id": "dana",
                "payload": {"arc_id": "arc_alma_dana", "goal_id": "goal_42"},
            },
            {"alma": "Alma", "dana": "Dana"},
            480,
        )
        self.assertNotIn("story_arc_started", premise)
        self.assertNotIn("arc_alma_dana", premise)
        self.assertNotIn("goal_42", premise)
        self.assertIn("Alma", premise)
        self.assertIn("Dana", premise)

    def test_day_one_sources_are_materialized_public_events_not_story_arcs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(db_path) as store:
                create_demo_world(store, MockProvider(), seed=7)
                # Bootstrap creates only internal story arcs. The skeleton
                # preparation must materialize the three public Day 1 events.
            graph = WorldSceneService(
                db_path, provider_factory=MockProvider, advance_minutes=0
            ).prepare_daily_story_skeleton(1)
            self.assertEqual(len(graph.source_event_ids), 3)
            with WorldStore(db_path) as store:
                selected = [
                    next(event for event in store.list_events() if event["event_id"] == event_id)
                    for event_id in graph.source_event_ids
                ]
                self.assertTrue(all(event["event_type"] == "character_story_stage" for event in selected))
                self.assertFalse(any("story_arc_started" in repr(event) for event in selected))

    def test_daily_source_events_use_explicit_narrative_allowlist(self) -> None:
        events = [
            {"event_id": 1, "event_type": "story_arc_started", "actor_id": "alma", "target_id": "dana", "payload": {}},
            {"event_id": 2, "event_type": "setup", "actor_id": "stella", "target_id": "sei", "payload": {}},
            {"event_id": 3, "event_type": "goal_created", "actor_id": "sei", "target_id": "stella", "payload": {}},
            {"event_id": 4, "event_type": "public_world_event", "actor_id": "alma", "target_id": "stella", "payload": {}},
            {"event_id": 5, "event_type": "worked", "actor_id": "dana", "target_id": "dorothy", "payload": {}},
        ]
        selected = WorldSceneService._daily_source_events(events)
        self.assertEqual([event["event_type"] for event in selected], ["worked", "public_world_event"])

    def test_story_day_parser_handles_all_scene_id_forms(self) -> None:
        for scene_id in (
            "day_1_customer_1_order",
            "pre_opening_day_1",
            "break_day_1",
            "music_selection_day_1",
            "opening_day_1",
        ):
            with self.subTest(scene_id=scene_id):
                self.assertEqual(WorldSceneService._story_day_for_scene(scene_id), 1)

    def test_generated_context_marks_first_and_second_half_of_vanilla_shift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            provider = RecordingProvider()
            WorldSceneService(
                db_path,
                provider_factory=lambda: provider,
                advance_minutes=0,
            ).prepare_daily_story_graph(1)

            directions = [
                context.scene_direction
                for context in (*provider.dialogue_contexts, *provider.player_contexts)
                if context.scene_direction is not None
            ]
            self.assertTrue(any(item.shift_phase == "first_half" for item in directions))
            self.assertTrue(any(item.shift_phase == "second_half" for item in directions))
            first_half = [
                item for item in directions if item.shift_phase == "first_half"
            ]
            self.assertTrue(first_half)
            self.assertTrue(
                all(
                    item.music_policy == "continue_selected_shift_music"
                    for item in first_half
                )
            )
            second_half = [
                item for item in directions if item.shift_phase == "second_half"
            ]
            self.assertTrue(second_half)
            self.assertTrue(
                all(item.music_policy == "reuse_playlist_after_break" for item in second_half)
            )
            self.assertTrue(
                all(item.break_save == "resume_after_native_save" for item in second_half)
            )

    def test_local_fallback_keeps_a_concrete_callback_without_stock_closers(self) -> None:
        scene = WorldSceneService._fallback_scene(
            {
                "event_id": 1,
                "event_type": "worked",
                "actor_id": "dana",
                "target_id": "dorothy",
            },
            {"dana": "Dana", "dorothy": "Dorothy"},
            480,
        )
        text = "\n".join(line.text for line in scene.lines)
        for stock in (
            "我先找个位置坐",
            "吧台一直在这儿",
            "音乐不错",
            "刚才那件事",
            "话题不用跟着杯子",
            "下一轮再接着说",
            "刚才提到的安排",
            "还没说完",
            "先放到明天再想",
        ):
            self.assertNotIn(stock, text)
        self.assertEqual(len(scene.lines), 3)
        self.assertNotIn(scene.order.requested_name if scene.order else "", scene.lines[1].text)
        self.assertEqual(scene.lines[2].text, scene.order.display_text if scene.order else "")
        self.assertNotIn("听说", text)
        self.assertNotIn("今晚还真让人碰上了", text)

    def test_fallback_departures_vary_by_durable_event_identity(self) -> None:
        first = WorldSceneService._fallback_departure("alma", "安排", None, 21)
        repeat = WorldSceneService._fallback_departure("alma", "安排", None, 21)
        next_day = WorldSceneService._fallback_departure("alma", "安排", None, 22)
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, next_day)
        self.assertTrue(first.startswith("我先走了"))
        self.assertTrue(next_day.startswith("我先走了"))

        anchored = WorldSceneService._fallback_scene(
            {
                "event_id": 2,
                "event_type": "worked",
                "actor_id": "dana",
                "target_id": "dorothy",
            },
            {"dana": "Dana", "dorothy": "Dorothy"},
            480,
            event_topic="Dana刚结束工作并拿到120信用点，今晚想确认这笔钱该怎么用。",
        )
        self.assertIn("Dana刚结束工作并拿到120信用点", anchored.lines[0].text)
        self.assertNotIn(anchored.order.requested_name if anchored.order else "", anchored.lines[1].text)
        self.assertEqual(anchored.lines[2].text, anchored.order.display_text if anchored.order else "")
        self.assertLessEqual(len(anchored.lines[1].text), 72)
        reaction = WorldSceneService._fallback_reaction(
            DrinkOrder("order_1", "alma", "moonblast", "Moonblast", ("strong",), AlcoholRequirement.REQUIRED, "Jill，一杯 Moonblast。"),
            WorldSceneService._candidate_result(
                DrinkOrder("order_1", "alma", "moonblast", "Moonblast", ("strong",), AlcoholRequirement.REQUIRED, "Jill，一杯 Moonblast。"),
                ServiceCategory.EXACT,
            ),
            9,
            event_topic="市中心交通线路临时调整",
            unresolved_question="Alma是否会改变明早的路线",
        )
        reaction_text = "\n".join(line.text for line in reaction.lines)
        self.assertEqual(len(reaction.lines), 3)
        self.assertNotIn("你刚才说的", reaction_text)
        self.assertNotIn("先放到明天再想", reaction_text)
        self.assertNotIn("味道对了", reaction_text)
        self.assertIn("明早见客户的安排", reaction_text)
        self.assertNotIn("市中心交通线路临时调整", reaction_text)
        self.assertIn("先走了", reaction.lines[-1].text)
        self.assertNotIn("回头再聊", reaction_text)
        self.assertLessEqual(max(len(line.text) for line in reaction.lines), 72)

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

    @classmethod
    def _ack_opening_gates(cls, service: WorldSceneService, prefix: str) -> None:
        """Advance the Stage 19 pre-opening and music gates."""
        for index in (1, 2):
            scene = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": f"{prefix}-gate-open-{index}",
                    "client_session_id": "story-session-0001",
                }
            )
            service.ack_scene(
                cls._ack(scene.scene_id, f"{prefix}-gate-ack-{index}", "continued_in_bar")
            )

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

    def test_stage19_opens_with_preparation_music_and_midshift_break(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            service.prepare_story_day({"request_id": "stage19-prepare"})

            def open_scene(request_id: str):
                return service.open_scene(
                    {
                        "protocol_version": 1,
                        "request_id": request_id,
                        "client_session_id": "stage19-session",
                    }
                )

            opening = open_scene("stage19-opening")
            service.ack_scene(self._ack(opening.scene_id, "stage19-a1", "continued_in_bar"))
            doorbell = open_scene("stage19-doorbell")
            service.ack_scene(self._ack(doorbell.scene_id, "stage19-a2", "continued_in_bar"))
            pre_opening = open_scene("stage19-pre")
            self.assertEqual(pre_opening.scene_id, "pre_opening_day_1")
            self.assertGreaterEqual(len(pre_opening.lines), 2)
            service.ack_scene(self._ack(pre_opening.scene_id, "stage19-a3", "continued_in_bar"))
            music = open_scene("stage19-music")
            self.assertEqual(music.scene_id, "music_selection_day_1")
            # The Python cursor is not advanced by opening the gate; the game
            # client must acknowledge it only after the vanilla jukebox READY
            # button closes the native UI.
            service.ack_scene(self._ack(music.scene_id, "stage19-a4", "continued_in_bar"))
            first = open_scene("stage19-first")
            self.assertIsNotNone(first.order)
            self.assertGreaterEqual(len(first.lines), 3)

    def test_stage19_ignores_stale_break_marker_before_customer_three(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            service.prepare_story_day({"request_id": "stage19-stale-prepare"})
            with WorldStore(db_path) as store:
                store.set_meta("break_pending_day_1", "1")

            scene = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "stage19-stale-open",
                    "client_session_id": "stage19-stale-session",
                }
            )
            self.assertNotEqual(scene.scene_id, "break_day_1")
            with WorldStore(db_path) as store:
                self.assertEqual(store.get_meta("break_pending_day_1"), "0")

    def test_stage19_interlude_and_result_closing_use_natural_role_aware_lines(self) -> None:
        pre_opening = WorldSceneService._daily_interlude_scene(1, "pre_opening")
        pre_opening_text = "".join(line.text for line in pre_opening.lines)
        self.assertNotIn("老板我自己", pre_opening_text)
        self.assertNotIn("先把吧台准备好，灯光我来处理", pre_opening_text)
        self.assertNotIn(
            "下一轮我会听清楚你的要求",
            WorldSceneService._result_closing(
                ServiceResult(
                    "order-1",
                    "dana",
                    ServiceCategory.EXACT,
                    "beer",
                    "Beer",
                    True,
                )
            ),
        )

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
            self._ack_opening_gates(service, "on-demand")
            self.assertEqual((provider.dialogue_calls, provider.player_calls), (2, 2))

            arrival = service.open_scene(
                {"protocol_version": 1, "request_id": "on-demand-open-3", "client_session_id": "story-session-0001"}
            )
            # The arrival now establishes a concrete character event before
            # the order interrupts it: two customer beats and three short Jill
            # replies are generated only when this scene is actually reached.
            self.assertEqual((provider.dialogue_calls, provider.player_calls), (4, 5))
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
            self.assertEqual((provider.dialogue_calls, provider.player_calls), (6, 6))
            reaction_direction = provider.dialogue_contexts[-1].scene_direction
            assert reaction_direction is not None
            self.assertNotIn("刚才提到的安排", reaction_direction.unresolved_question)
            self.assertIn("接下来会怎样", reaction_direction.unresolved_question)

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
                WorldSceneService._ensure_day_one_public_events(store, 1)
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

    def test_stage21_migrates_old_active_day_back_to_opening(self) -> None:
        old_version = "stage_19_full_day_v1"
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            with WorldStore(db_path) as store:
                create_demo_world(store, MockProvider(), seed=7)
                WorldSceneService._ensure_day_one_public_events(store, 1)
                events = WorldSceneService._daily_source_events(store.list_events())
                source_ids = tuple(int(event["event_id"]) for event in events)
                store.begin_daily_story_graph(1, old_version, store.current_tick, source_ids)
                old_graph = service._build_daily_story_skeleton(
                    1,
                    store.current_tick,
                    events,
                    {agent.agent_id: agent.display_name for agent in store.list_agents()},
                )
                old_graph = old_graph.to_dict()
                old_graph["generation_version"] = old_version
                store.complete_daily_story_graph(1, old_version, old_graph)
                store.advance_daily_story_cursor(
                    1, old_version, "day_1_customer_1_arrival", "day_1_customer_1_exact"
                )
                store.set_meta("bridge_ack:opening_day_1", "1")
                store.set_meta("bridge_ack:doorbell_day_1", "2")
                store.set_meta("bridge_ack:pre_opening_day_1", "3")
                store.set_meta("bridge_ack:music_selection_day_1", "4")
                store.set_meta("player_shift_income", "250")

            prepared = service.prepare_story_day(
                {"request_id": "stage21-migration", "client_session_id": "migration-session"}
            )
            self.assertEqual(prepared["world_day"], 1)
            with WorldStore(db_path) as store:
                self.assertIsNone(store.get_daily_story_graph(1, old_version))
                current = store.get_daily_story_progress(1, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current["current_node_id"], "day_1_customer_1_arrival")
                self.assertIsNone(store.get_meta("bridge_ack:opening_day_1"))
                self.assertEqual(store.get_meta("player_shift_income"), "0")

    def test_stage21_migration_clears_all_current_day_receipts_but_preserves_history(self) -> None:
        old_version = "stage_19_full_day_v1"
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            with WorldStore(db_path) as store:
                create_demo_world(store, MockProvider(), seed=7)
                WorldSceneService._ensure_day_one_public_events(store, 1)
                events = WorldSceneService._daily_source_events(store.list_events())
                source_ids = tuple(int(event["event_id"]) for event in events)
                names = {agent.agent_id: agent.display_name for agent in store.list_agents()}
                prior_graph = service._build_daily_story_skeleton(1, 0, events, names).to_dict()
                prior_graph["generation_version"] = old_version
                store.begin_daily_story_graph(1, old_version, 0, source_ids)
                store.complete_daily_story_graph(1, old_version, prior_graph)
                store.advance_daily_story_cursor(
                    1, old_version, "day_1_customer_1_arrival", "day_1_customer_1_exact"
                )
                with store.transaction():
                    store._conn.execute(
                        "UPDATE daily_story_progress SET status = 'completed', current_node_id = NULL "
                        "WHERE day_index = 1 AND generation_version = ?",
                        (old_version,),
                    )

                active_graph = service._build_daily_story_skeleton(2, 0, events, names).to_dict()
                active_graph["generation_version"] = old_version
                store.begin_daily_story_graph(2, old_version, 0, source_ids)
                store.complete_daily_story_graph(2, old_version, active_graph)
                store.advance_daily_story_cursor(
                    2, old_version, "day_2_customer_1_arrival", "day_2_customer_1_exact"
                )
                # A target-version draft may already exist when an interrupted
                # upgrade is retried; migration must remove it with the old one.
                target_graph = service._build_daily_story_skeleton(2, 0, events, names).to_dict()
                store.begin_daily_story_graph(2, DAILY_STORY_GRAPH_VERSION, 0, source_ids)
                store.complete_daily_story_graph(2, DAILY_STORY_GRAPH_VERSION, target_graph)

                service_event_id = store.append_event(
                    0,
                    "drink_served",
                    "stella",
                    payload={"story_day": 2, "scene_id": "day_2_customer_3_order"},
                )
                current_transcript = store.append_event(
                    0,
                    "dialogue_transcript",
                    None,
                    payload={
                        "story_day": 2,
                        "scene_id": "day_2_customer_3_order",
                        "lines": [{"line_id": "dialogue_1", "speaker_id": "stella", "text": "交通线路改了。"}],
                    },
                )
                store.record_story_branch_commit(
                    day_index=2,
                    generation_version=old_version,
                    order_id="order_day_2_3",
                    arrival_node_id="day_2_customer_3_arrival",
                    result_node_id="day_2_customer_3_exact",
                    category="exact",
                    service_event_id=service_event_id,
                    income_delta=180,
                )
                for key, value in {
                    "bridge_ack:opening_day_2": "1",
                    "bridge_ack:pre_opening_day_2": "1",
                    "bridge_ack:music_selection_day_2": "1",
                    "bridge_ack:break_day_2": "1",
                    "bridge_scene:day_2_customer_3_order": "2",
                    "bridge_scene_payload:day_2_customer_3_order": json.dumps({"story_day": 2}),
                    "bridge_open:old-request": json.dumps({"story_day": 2}),
                    "bridge_order:order_day_2_3": json.dumps({"story_day": 2}),
                    "bridge_order_request:order_day_2_3": json.dumps({"story_day": 2}),
                    "story_scene_node:day_2_customer_3_order": json.dumps({"day_index": 2}),
                    "story_materialized_scene:day_2_customer_3_order": json.dumps({"story_day": 2}),
                    "break_pending_day_2": "1",
                    "music_selected_day_2": "1",
                }.items():
                    store.set_meta(key, value)
                store.set_meta("current_story_day", "2")
                store.set_meta("player_shift_income", "180")
                historical_relationship = store.get_relationship("dana", "dorothy")
                historical_event = store.append_event(0, "long_term_marker", "dana", "dorothy")
                historical_transcript = store.append_event(
                    0,
                    "dialogue_transcript",
                    None,
                    payload={"story_day": 1, "scene_id": "day_1_customer_1_order", "lines": []},
                )

            prepared = service.prepare_story_day(
                {"request_id": "stage21-history-migration", "client_session_id": "migration-session"}
            )
            self.assertEqual(prepared["world_day"], 2)
            with WorldStore(db_path) as store:
                self.assertIsNone(store.get_daily_story_graph(2, old_version))
                target = store.get_daily_story_graph(2, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(target)
                assert target is not None
                self.assertEqual(target["status"], "ready")
                current = store.get_daily_story_progress(2, DAILY_STORY_GRAPH_VERSION)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current["current_node_id"], "day_2_customer_1_arrival")
                historical_graph = store.get_daily_story_graph(1, old_version)
                self.assertIsNotNone(historical_graph)
                historical_progress = store.get_daily_story_progress(1, old_version)
                self.assertIsNotNone(historical_progress)
                assert historical_progress is not None
                self.assertEqual(historical_progress["status"], "completed")
                self.assertEqual(store.get_meta("bridge_ack:opening_day_2"), None)
                self.assertEqual(store.get_meta("break_pending_day_2"), None)
                self.assertIsNone(store.get_meta("bridge_order:order_day_2_3"))
                self.assertEqual(store.get_meta("player_shift_income"), "0")
                self.assertIsNotNone(next((event for event in store.list_events() if event["event_id"] == historical_event), None))
                self.assertIsNotNone(next((event for event in store.list_events() if event["event_id"] == historical_transcript), None))
                self.assertIsNone(next((event for event in store.list_events() if event["event_id"] == current_transcript), None))
                self.assertEqual(store.get_relationship("dana", "dorothy"), historical_relationship)

                # A second pass is a no-op and does not erase the newly-created graph.
                self.assertIsNone(store.migrate_incompatible_daily_story(2, DAILY_STORY_GRAPH_VERSION))
                self.assertIsNotNone(store.get_daily_story_graph(2, DAILY_STORY_GRAPH_VERSION))

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

            # Day 1 now materializes its three code-owned public events as the
            # authoritative source catalogue before building the skeleton.
            before_events = before["events"]
            self._prepare(db_path)
            with WorldStore(db_path) as store:
                after_events = store.list_events()
                self.assertEqual(
                    [event["event_type"] for event in after_events[: len(before_events)]],
                    [event["event_type"] for event in before_events],
                )
                self.assertEqual(
                    len(after_events) - len(before_events),
                    9,
                )
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
            self._ack_opening_gates(service, "story")
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

    def test_provider_failure_uses_local_reaction_and_commits_order(self) -> None:
        class FailingReactionProvider:
            @staticmethod
            def decide(context):
                return MockProvider().decide(context)

            @staticmethod
            def generate_dialogue_line(context):
                raise BYOKTransportError("synthetic reaction transport failure")

            @staticmethod
            def generate_player_dialogue_line(context):
                raise BYOKTransportError("synthetic reaction transport failure")

        # Graph preparation, pre-opening, arrival, and reaction each obtain a
        # provider independently. Fail only the final reaction generation so
        # the test exercises settlement recovery rather than arrival fallback.
        providers = [
            MockProvider(),
            MockProvider(),
            MockProvider(),
            FailingReactionProvider(),
        ]

        def provider_factory():
            return providers.pop(0) if providers else FailingReactionProvider()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=provider_factory,
                advance_minutes=0,
                daily_story_mode=True,
                # Dialogue provider failure must not roll back the already
                # committed local drink result.
                allow_provider_fallback=False,
            )
            graph = service.prepare_daily_story_graph(1)
            self.assertEqual(graph.day_index, 1)
            opening = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "fallback-open-1",
                    "client_session_id": "fallback-session-0001",
                }
            )
            service.ack_scene(
                self._ack(opening.scene_id, "fallback-ack-1", "continued_in_bar")
            )
            doorbell = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "fallback-open-2",
                    "client_session_id": "fallback-session-0001",
                }
            )
            service.ack_scene(
                self._ack(doorbell.scene_id, "fallback-ack-2", "continued_in_bar")
            )
            self._ack_opening_gates(service, "fallback")
            arrival = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "fallback-open-3",
                    "client_session_id": "fallback-session-0001",
                }
            )
            assert arrival.order is not None
            service.ack_scene(
                self._ack(arrival.scene_id, "fallback-ack-3", "order_started")
            )
            request = {
                "protocol_version": 1,
                "request_id": "fallback-order-1",
                "client_session_id": "fallback-session-0001",
                "scene_id": arrival.scene_id,
                "order_id": arrival.order.order_id,
                "drink": self._exact_drink(arrival.order.requested_drink_id),
            }
            resolution = service.resolve_order(request)
            replay = service.resolve_order(request)
            self.assertEqual(resolution, replay)
            self.assertEqual(resolution.result.category, ServiceCategory.EXACT)
            self.assertTrue(resolution.scene.scene_id.startswith("day_1_customer_1_exact"))
            self.assertGreater(resolution.income_delta, 0)
            with WorldStore(db_path) as store:
                commits = store.list_story_branch_commits()
                self.assertEqual(len(commits), 1)
                served = [event for event in store.list_events() if event["event_type"] == "drink_served"]
                self.assertEqual(len(served), 1)
                fallback_events = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "dialogue_provider_fallback"
                ]
                self.assertEqual(len(fallback_events), 1)
                self.assertEqual(
                    fallback_events[0]["payload"]["error_type"],
                    "BYOKTransportError",
                )

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

    def test_fallback_reaction_covers_every_service_category(self) -> None:
        order = DrinkOrder(
            "order_boomlight",
            "alma",
            "boomlight",
            "Boomlight",
            ("strong",),
            AlcoholRequirement.REQUIRED,
            "Jill，一杯 Boomlight。",
        )
        for index, category in enumerate(ServiceCategory, start=1):
            result = WorldSceneService._candidate_result(order, category)
            scene = WorldSceneService._fallback_reaction(order, result, index)
            self.assertEqual(len(scene.lines), 3)
            self.assertEqual(scene.lines[0].speaker_id, "alma")
            self.assertTrue(scene.lines[0].text)

        long_topic = "市中心交通线路临时调整，施工封闭让两条常用线路绕开酒吧附近街区，预计几天内逐步恢复。"
        arrival = WorldSceneService._fallback_scene(
            {"event_id": 7, "event_type": "public_world_event", "actor_id": "alma", "target_id": "stella"},
            {"alma": "Alma", "stella": "Stella"},
            0,
            event_topic=long_topic,
        )
        self.assertLessEqual(max(len(line.text) for line in arrival.lines), 72)

    def test_fallback_reaction_uses_character_specific_short_beats(self) -> None:
        names = {"alma": "Alma", "sei": "Sei", "dorothy": "Dorothy"}
        event_ids = {item.event_key: index for index, item in enumerate(CODE_OWNED_DAY_ONE_EVENTS, start=1)}
        cases = (
            ("alma", "city_news_day_1_transit", "明早见客户的安排"),
            ("sei", "city_news_day_1_night_market", "明晚那条接人路线"),
            ("dorothy", "city_news_day_1_weather", "那场被积水耽误的约见"),
        )
        rendered: list[str] = []
        for customer, event_key, short_topic in cases:
            event = next(item for item in CODE_OWNED_DAY_ONE_EVENTS if item.event_key == event_key)
            perspective = WorldSceneService._perspective_for_event(
                event.to_dict() | {"event_id": event_ids[event_key]}, names, 0
            )
            order = DrinkOrder(
                f"order_{customer}", customer, "moonblast", "Moonblast",
                ("strong",), AlcoholRequirement.REQUIRED, "Jill，一杯 Moonblast。",
            )
            result = WorldSceneService._candidate_result(order, ServiceCategory.EXACT)
            scene = WorldSceneService._fallback_reaction(
                order, result, event_ids[event_key],
                event_topic=perspective.event_topic,
                personal_stake=perspective.personal_stake,
                unresolved_question=perspective.unresolved_question,
            )
            text = "\n".join(line.text for line in scene.lines)
            rendered.append(text)
            self.assertIn(short_topic, text)
            self.assertNotIn(perspective.event_topic, text)
            self.assertNotIn("回头再聊", text)
            self.assertNotIn("我先走了，" + perspective.event_topic, text)
            self.assertTrue(scene.lines[-1].text.startswith("我先走了"))
            self.assertLessEqual(max(len(line.text) for line in scene.lines), 72)
            self.assertNotRegex(text, r"story_arc_started|goal_id|arc_id")
        self.assertEqual(len(set(rendered)), 3)

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

    def test_first_scene_transcript_is_complete_and_ack_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            service = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            scene = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "transcript-open-1",
                    "client_session_id": "transcript-session",
                }
            )
            ack = self._ack(scene.scene_id, "transcript-ack-1", "continued_in_bar")
            ack["client_session_id"] = "transcript-session"
            service.ack_scene(ack)
            service.ack_scene(ack)
            with WorldStore(db_path) as store:
                records = [
                    event for event in store.list_events()
                    if event["event_type"] == "dialogue_transcript"
                ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["payload"]["scene_id"], scene.scene_id)
            self.assertEqual(records[0]["payload"]["story_day"], 1)
            self.assertEqual(
                records[0]["payload"]["lines"],
                [
                    {
                        "line_id": line.line_id,
                        "speaker_id": line.speaker_id or "",
                        "text": line.text,
                    }
                    for line in scene.lines
                ],
            )

    def test_generation_failure_is_reported_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"

            def failing_factory():
                raise RuntimeError("private diagnostic")

            reports: list[tuple[str, Exception]] = []

            service = WorldSceneService(
                db_path,
                provider_factory=failing_factory,
                error_reporter=lambda operation, error: reports.append(
                    (operation, error)
                ),
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
            # Provider failure is contained in the player flow and produces a
            # local scene while the diagnostic remains available.
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
            self._ack_opening_gates(service, "failure")
            arrival = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "failure-open-3",
                    "client_session_id": "story-session-0001",
                }
            )
            self.assertIsNotNone(arrival.order)
            self.assertTrue(
                any(
                    operation == "pre-opening dialogue provider fallback"
                    for operation, _ in reports
                )
            )
            self.assertTrue(
                any(
                    operation == "arrival dialogue provider fallback"
                    for operation, _ in reports
                )
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
            self._ack_opening_gates(service, "day-loop")

            arrivals = [
                node for node in graph.nodes if node.kind is StoryNodeKind.ARRIVAL_ORDER
            ]
            expected_income = 0
            for index, _ in enumerate(arrivals, start=1):
                if index == 3:
                    break_scene = opened()
                    self.assertEqual(break_scene.scene_id, "break_day_1")
                    service.ack_scene(
                        self._ack(
                            break_scene.scene_id,
                            "day-loop-ack-break",
                            "continued_in_bar",
                        )
                    )
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
                if index == 2:
                    self.assertIn("先走了", resolution.scene.lines[-1].text)
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
