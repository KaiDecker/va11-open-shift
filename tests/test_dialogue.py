from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from open_shift.byok import (
    APIProtocol,
    BYOKBudgetExceeded,
    BYOKConfig,
    BYOKProvider,
    ResponseFormat,
    ThinkingMode,
)
from open_shift.bridge import BridgeError
from open_shift.dialogue import (
    CHARACTER_PROFILES,
    DIALOGUE_SYSTEM_INSTRUCTION,
    MAX_DIALOGUE_CHARACTERS,
    PUBLIC_CHARACTER_IDENTITIES,
    DialogueLineDraft,
    DialogueTurnContext,
    DialogueUtterance,
    PlayerDialogueTurnContext,
    dialogue_observation,
    player_dialogue_observation,
    validate_dialogue_output,
    validate_player_dialogue_output,
)
from open_shift.engine import SimulationEngine
from open_shift.drinks import DRINK_RECIPES, ServiceCategory
from open_shift.lore import (
    CANON_SOURCE_URLS,
    CHARACTER_LORE,
    JILL_LORE,
    ORIGINAL_DIALOGUE_STYLE,
    CONTINUITY_FACTS,
)
from open_shift.models import (
    AgentState,
    DecisionContext,
    Goal,
    Memory,
    Relationship,
)
from open_shift.providers import MockProvider
from open_shift.store import WorldStore
from open_shift.world_bridge import WorldSceneService


def private_context() -> DecisionContext:
    dana = AgentState("dana", "Dana", "va11_hall_a", 90, 0.2, "steady", 480)
    dorothy = AgentState(
        "dorothy", "Dorothy", "secret_location", 999, 0.8, "private_mood", 600
    )
    return DecisionContext(
        tick=1440,
        seed=7,
        actor=dana,
        agents=(dana, dorothy),
        relationships=(Relationship("dana", "dorothy", 0.2, 0.3),),
        goals=(Goal("dana_savings", "dana", "savings", None, 150, 0.7),),
        locations=("home", "work", "va11_hall_a"),
        memories=(
            Memory(
                1,
                1,
                1200,
                0.8,
                "Dana privately remembers Dorothy checking in.",
                ("social", "dorothy"),
            ),
        ),
    )


def turn_context() -> DialogueTurnContext:
    return DialogueTurnContext(
        scene_id="world_event_1",
        turn_index=0,
        turn_count=4,
        premise="第2天，Dana结束工作后在酒吧遇到了Dorothy。",
        speaker=private_context(),
        participant_ids=("dana", "dorothy"),
    )


def player_turn_context() -> PlayerDialogueTurnContext:
    return PlayerDialogueTurnContext(
        "world_event_1",
        1,
        3,
        "Dorothy 在吧台点了一杯甜酒。",
        ("dorothy", "jill"),
        (DialogueUtterance("dorothy", "Jill，来杯甜的。"),),
    )


class FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


class RecordingDialogueProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.policy = MockProvider()
        self.fail = fail
        self.dialogue_contexts: list[DialogueTurnContext] = []
        self.player_contexts: list[PlayerDialogueTurnContext] = []

    def decide(self, context: DecisionContext):
        return self.policy.decide(context)

    def generate_dialogue_line(
        self, context: DialogueTurnContext
    ) -> DialogueLineDraft:
        self.dialogue_contexts.append(context)
        if self.fail:
            raise RuntimeError("synthetic dialogue failure")
        name = context.speaker.actor.display_name
        return DialogueLineDraft(
            "neutral" if context.turn_index % 2 else "happy",
            f"这是{name}根据自己的记忆作出的第{context.turn_index + 1}次回应。",
        )

    def generate_player_dialogue_line(
        self, context: PlayerDialogueTurnContext
    ) -> DialogueLineDraft:
        self.player_contexts.append(context)
        if self.fail:
            raise RuntimeError("synthetic player dialogue failure")
        return DialogueLineDraft("neutral", "知道了。先让我把杯子准备好。")


class DialogueContractTests(unittest.TestCase):
    def test_profiles_cover_exactly_the_game_speaker_whitelist(self) -> None:
        self.assertEqual(
            set(CHARACTER_PROFILES),
            {"dana", "dorothy", "alma", "stella", "sei"},
        )
        self.assertNotIn("jill", CHARACTER_PROFILES)
        self.assertEqual(
            set(PUBLIC_CHARACTER_IDENTITIES), set(CHARACTER_PROFILES) | {"jill"}
        )
        self.assertEqual(set(CHARACTER_LORE), set(CHARACTER_PROFILES))
        self.assertIn("调酒师", JILL_LORE.public_identity)
        self.assertEqual(MAX_DIALOGUE_CHARACTERS, 72)
        self.assertIn("https://waifubartending.com/", CANON_SOURCE_URLS)
        self.assertIn("https://vndb.org/v18872", CANON_SOURCE_URLS)
        self.assertIn(
            "https://va11halla.fandom.com/zh/wiki/VA-11_HALL-A_Wiki",
            CANON_SOURCE_URLS,
        )
        self.assertTrue(CONTINUITY_FACTS)
        self.assertTrue(ORIGINAL_DIALOGUE_STYLE)
        self.assertNotIn("Classy", repr(CHARACTER_LORE["stella"]))

    def test_observation_contains_only_current_speaker_private_state(self) -> None:
        observation = dialogue_observation(turn_context())
        encoded = json.dumps(observation, ensure_ascii=False)
        self.assertEqual(observation["speaker"]["agent_id"], "dana")
        self.assertIn("stable_core", observation["speaker"]["canon"])
        self.assertIn("voice", observation["speaker"]["canon"])
        self.assertIn("drink_preferences", observation["speaker"]["canon"])
        self.assertEqual(
            observation["private_relevant_memories"][0]["summary"],
            "Dana privately remembers Dorothy checking in.",
        )
        dorothy = next(
            item for item in observation["participants"] if item["agent_id"] == "dorothy"
        )
        self.assertEqual(
            set(dorothy), {"agent_id", "display_name", "public_identity"}
        )
        self.assertIn("Lilim", dorothy["public_identity"])
        self.assertIn("彼此认识的熟人", DIALOGUE_SYSTEM_INSTRUCTION)
        self.assertIn("不得询问", DIALOGUE_SYSTEM_INSTRUCTION)
        self.assertIn("不可自行改写", DIALOGUE_SYSTEM_INSTRUCTION)
        self.assertIn("通用客服式句型", DIALOGUE_SYSTEM_INSTRUCTION)
        self.assertIn("唯一执行调酒", DIALOGUE_SYSTEM_INSTRUCTION)
        self.assertIn("Dana", DIALOGUE_SYSTEM_INSTRUCTION)
        for forbidden_meta_term in (
            "原版",
            "好结局",
            "续篇",
            "模组",
            "时间线",
        ):
            self.assertNotIn(forbidden_meta_term, encoded)
        self.assertIn("original_dialogue_style", observation)
        self.assertNotIn("money", observation["speaker"])
        self.assertNotIn("world_tick", observation["scene"])
        self.assertNotIn("world_day", observation["scene"])
        self.assertNotIn("target_value", encoded)
        self.assertNotIn("secret_location", encoded)
        self.assertNotIn("private_mood", encoded)
        self.assertNotIn("999", encoded)

    def test_output_validation_rejects_wrong_fields_and_allows_addressing_jill(self) -> None:
        context = turn_context()
        accepted = validate_dialogue_output(
            {"expression_id": "happy", "text": "今天看起来会顺利一些。"},
            context,
        )
        self.assertEqual(accepted.expression_id, "happy")
        addressed = validate_dialogue_output(
            {"expression_id": "happy", "text": "Jill，来谈谈吧。"},
            context,
        )
        self.assertIn("Jill", addressed.text)
        for invalid in (
            {"expression_id": "angry", "text": "今天看起来会顺利一些。"},
            {"expression_id": "happy", "text": "This is English."},
            {"expression_id": "happy", "text": "Dana：今天聊聊吧。"},
            {"expression_id": "happy", "text": "这是原版好结局之后。"},
            {"expression_id": "happy", "text": "第一行#第二行。"},
            {"expression_id": "happy", "text": "长" * 73},
            {"expression_id": "happy", "text": "今天聊聊吧。", "extra": 1},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_dialogue_output(invalid, context)

    def test_only_jill_may_claim_bartending_actions(self) -> None:
        context = turn_context()
        for allowed in (
            "Jill，这杯还是交给你。",
            "Jill，你调的这杯很合适。",
            "那就看你的了，Jill。",
        ):
            with self.subTest(allowed=allowed):
                draft = validate_dialogue_output(
                    {"expression_id": "neutral", "text": allowed}, context
                )
                self.assertEqual(draft.text, allowed)

        for forbidden in (
            "行，我先调酒，你继续说。",
            "这杯我来，Jill 去歇会儿。",
            "我给你调一杯。",
            "我来把这杯摇好。",
            "今晚由我出杯。",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                validate_dialogue_output(
                    {"expression_id": "neutral", "text": forbidden}, context
                )

    def test_player_dialogue_has_no_private_agent_state_or_portrait_role(self) -> None:
        context = PlayerDialogueTurnContext(
            "world_event_1",
            1,
            3,
            "Dorothy 在吧台点了一杯酒。",
            ("dorothy", "jill"),
            (DialogueUtterance("dorothy", "Jill，来杯甜的。"),),
        )
        observation = player_dialogue_observation(context)
        encoded = json.dumps(observation, ensure_ascii=False)
        self.assertEqual(observation["speaker"]["speaker_id"], "jill")
        self.assertEqual(observation["speaker"]["role"], "player_bartender")
        self.assertNotIn("private_relevant_memories", encoded)
        self.assertNotIn("relationships", encoded)
        draft = validate_player_dialogue_output(
            {"expression_id": "neutral", "text": "甜的。要求还挺宽。"}, context
        )
        self.assertEqual(draft.expression_id, "neutral")
        with self.assertRaises(ValueError):
            validate_player_dialogue_output(
                {"expression_id": "happy", "text": "我知道了。"}, context
            )

    def test_byok_dialogue_uses_json_contract_and_shared_budget(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "expression_id": "worry",
                                    "text": "我确实有点担心，不过可以慢慢说。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.CHAT_COMPLETIONS,
                response_format=ResponseFormat.JSON_OBJECT,
                max_calls=1,
                thinking_mode=ThinkingMode.DISABLED,
            ),
            _api_key="secret",
            transport=transport,
        )
        result = provider.generate_dialogue_line(turn_context())
        self.assertEqual(result.expression_id, "worry")
        self.assertEqual(provider.calls_used, 1)
        call = transport.calls[0]
        self.assertEqual(call["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(call["payload"]["max_tokens"], 160)
        self.assertEqual(call["payload"]["thinking"], {"type": "disabled"})
        user_input = call["payload"]["messages"][1]["content"]
        self.assertIn("private_relevant_memories", user_input)
        self.assertNotIn("secret_location", user_input)
        self.assertNotIn("secret", repr(provider))
        with self.assertRaises(BYOKBudgetExceeded):
            provider.decide(private_context())
        self.assertEqual(len(transport.calls), 1)

    def test_byok_player_dialogue_uses_jill_only_contract(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "expression_id": "neutral",
                                    "text": "甜的。范围还挺宽。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.CHAT_COMPLETIONS,
                response_format=ResponseFormat.JSON_OBJECT,
                max_calls=1,
                thinking_mode=ThinkingMode.DISABLED,
            ),
            _api_key="secret",
            transport=transport,
        )
        result = provider.generate_player_dialogue_line(player_turn_context())
        self.assertEqual(result.expression_id, "neutral")
        call = transport.calls[0]["payload"]
        self.assertIn("玩家角色 Jill", call["messages"][0]["content"])
        self.assertIn("player_bartender", call["messages"][1]["content"])
        self.assertNotIn("private_relevant_memories", call["messages"][1]["content"])
        with self.assertRaises(BYOKBudgetExceeded):
            provider.generate_player_dialogue_line(player_turn_context())


class DialogueWorldBridgeTests(unittest.TestCase):
    @staticmethod
    def ack_request(scene_id: str, outcome: str = "order_started") -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "request_id": f"ack-{scene_id}",
            "client_session_id": "dialogue-session-1",
            "scene_id": scene_id,
            "outcome": outcome,
        }

    def test_legacy_timeline_label_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            with WorldStore(db_path) as store:
                store.set_meta("timeline_id", "legacy-stage-5")
            WorldSceneService(db_path, advance_minutes=0).open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "normalize-timeline-1",
                    "client_session_id": "dialogue-session-1",
                }
            )
            with WorldStore(db_path) as store:
                self.assertEqual(store.get_meta("timeline_id"), "after_main_story")

    def test_each_turn_uses_its_speakers_private_context_and_scene_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            provider = RecordingDialogueProvider()
            service = WorldSceneService(
                db_path,
                provider_factory=lambda: provider,
                advance_minutes=0,
            )
            request = {
                "protocol_version": 1,
                "request_id": "dialogue-open-1",
                "client_session_id": "dialogue-session-1",
            }
            scene = service.open_scene(request)
            self.assertEqual(len(scene.lines), 3)
            self.assertEqual(len(provider.dialogue_contexts), 1)
            self.assertEqual(len(provider.player_contexts), 1)
            self.assertIn("jill", {line.speaker_id for line in scene.lines})
            jill_line = next(line for line in scene.lines if line.speaker_id == "jill")
            self.assertIsNone(jill_line.portrait_id)
            self.assertIsNotNone(scene.order)
            context = provider.dialogue_contexts[0]
            self.assertEqual(len(context.transcript), context.turn_index)
            self.assertTrue(
                all(memory in context.speaker.memories for memory in context.speaker.memories)
            )
            with WorldStore(db_path) as store:
                self.assertEqual(store.get_meta("timeline_id"), "after_main_story")
                record = json.loads(store.get_meta("bridge_open:dialogue-open-1") or "{}")
                self.assertEqual(record["dialogue_version"], 2)
                self.assertEqual(record["scene"], scene.to_dict())

            replay_provider = RecordingDialogueProvider(fail=True)
            replay = WorldSceneService(
                db_path,
                provider_factory=lambda: replay_provider,
                advance_minutes=0,
            ).open_scene(request)
            self.assertEqual(replay, scene)
            self.assertEqual(replay_provider.dialogue_contexts, [])
            self.assertEqual(replay_provider.player_contexts, [])

            service.ack_scene(self.ack_request(scene.scene_id))
            service.ack_scene(self.ack_request(scene.scene_id))
            with WorldStore(db_path) as store:
                dialogue_events = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "agent_dialogue_completed"
                ]
                self.assertEqual(len(dialogue_events), 1)
                participant_ids = {
                    line.speaker_id for line in scene.lines if line.speaker_id != "jill"
                }
                for participant_id in participant_ids:
                    learned = [
                        memory
                        for memory in store.list_memories(participant_id)
                        if "dialogue" in memory["tags"]
                    ]
                    self.assertEqual(len(learned), 1)
                    self.assertIn("公开对话", learned[0]["summary"])
                    next_context = SimulationEngine(
                        store, MockProvider()
                    ).context_for_agent(store.current_tick, participant_id)
                    self.assertTrue(
                        any("公开对话" in memory.summary for memory in next_context.memories)
                    )

    def test_dialogue_failure_falls_back_and_is_not_retried_on_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            provider = RecordingDialogueProvider(fail=True)
            reports: list[tuple[str, Exception]] = []
            service = WorldSceneService(
                db_path,
                provider_factory=lambda: provider,
                error_reporter=lambda operation, error: reports.append(
                    (operation, error)
                ),
                advance_minutes=0,
            )
            request = {
                "protocol_version": 1,
                "request_id": "dialogue-fallback-1",
                "client_session_id": "dialogue-session-1",
            }
            first = service.open_scene(request)
            self.assertEqual(len(first.lines), 3)
            self.assertEqual(len(provider.dialogue_contexts), 0)
            self.assertEqual(len(provider.player_contexts), 1)
            second = service.open_scene(request)
            self.assertEqual(second, first)
            self.assertEqual(len(provider.dialogue_contexts), 0)
            self.assertEqual(len(provider.player_contexts), 1)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0][0], "dialogue generation")
            self.assertIsInstance(reports[0][1], RuntimeError)
            with WorldStore(db_path) as store:
                errors = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "dialogue_provider_error"
                ]
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["payload"]["error_type"], "RuntimeError")
            service.ack_scene(self.ack_request(first.scene_id))
            with WorldStore(db_path) as store:
                dialogue_events = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "agent_dialogue_completed"
                ]
                self.assertEqual(dialogue_events, [])

    def test_order_resolution_is_persisted_and_returns_a_jill_reaction_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            provider = RecordingDialogueProvider()
            service = WorldSceneService(
                db_path,
                provider_factory=lambda: provider,
                advance_minutes=0,
            )
            scene = service.open_scene(
                {
                    "protocol_version": 1,
                    "request_id": "order-open-1",
                    "client_session_id": "dialogue-session-1",
                }
            )
            assert scene.order is not None
            service.ack_scene(self.ack_request(scene.scene_id))
            recipe = next(
                item
                for item in DRINK_RECIPES
                if item.drink_id == scene.order.requested_drink_id
            )
            ingredients = list(recipe.ingredients)
            if recipe.optional_karmotrine and scene.order.alcohol_requirement.value == "required":
                ingredients[4] = 1
            request = {
                "protocol_version": 1,
                "request_id": "resolve-order-1",
                "client_session_id": "dialogue-session-1",
                "scene_id": scene.scene_id,
                "order_id": scene.order.order_id,
                "drink": {
                    "adelhyde": float(ingredients[0]),
                    "bronson_extract": float(ingredients[1]),
                    "powdered_delta": float(ingredients[2]),
                    "flanergide": float(ingredients[3]),
                    "karmotrine": float(ingredients[4]),
                    "ice": float(recipe.ice),
                    "aged": float(recipe.aged),
                    "preparation": recipe.preparation,
                },
            }
            resolution = service.resolve_order(request)
            replay = service.resolve_order(request)
            self.assertEqual(resolution, replay)
            self.assertEqual(resolution.result.category, ServiceCategory.EXACT)
            self.assertIsNone(resolution.scene.order)
            jill_line = next(
                line for line in resolution.scene.lines if line.speaker_id == "jill"
            )
            self.assertIsNone(jill_line.portrait_id)
            service.ack_scene(
                self.ack_request(resolution.scene.scene_id, "continued_in_bar")
            )
            with WorldStore(db_path) as store:
                served = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "drink_served"
                ]
                self.assertEqual(len(served), 1)
                self.assertEqual(
                    served[0]["payload"]["result"]["category"], "exact"
                )
                self.assertTrue(
                    all(
                        type(served[0]["payload"]["drink"][name]) is int
                        for name in (
                            "adelhyde",
                            "bronson_extract",
                            "powdered_delta",
                            "flanergide",
                            "karmotrine",
                            "ice",
                            "aged",
                        )
                    )
                )

            changed = dict(request)
            changed["request_id"] = "resolve-order-2"
            changed["drink"] = {**request["drink"], "adelhyde": 0}
            with self.assertRaises(BridgeError) as raised:
                service.resolve_order(changed)
            self.assertEqual(raised.exception.code, "order_already_resolved")

    def test_provider_budget_exhaustion_is_reported_instead_of_looking_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transport = FakeTransport(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "expression_id": "neutral",
                                        "text": "先从眼前这件小事说起吧。",
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }
            )
            provider = BYOKProvider(
                BYOKConfig(
                    "https://api.example.test/v1",
                    "test-model",
                    protocol=APIProtocol.CHAT_COMPLETIONS,
                    max_calls=1,
                ),
                _api_key="secret",
                transport=transport,
            )
            service = WorldSceneService(
                Path(temp_dir) / "world.sqlite3",
                provider_factory=lambda: provider,
                advance_minutes=0,
            )
            with self.assertRaises(BridgeError) as raised:
                service.open_scene(
                    {
                        "protocol_version": 1,
                        "request_id": "dialogue-budget-1",
                        "client_session_id": "dialogue-session-1",
                    }
                )
            self.assertEqual(raised.exception.status, 429)
            self.assertEqual(raised.exception.code, "provider_budget_exhausted")
            self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
