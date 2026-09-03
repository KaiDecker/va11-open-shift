from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from open_shift.byok import (
    APIProtocol,
    BYOKBudgetExceeded,
    BYOKConfig,
    BYOKProvider,
    BYOKValidationError,
    ResponseFormat,
    ThinkingMode,
)
from open_shift.bridge import BridgeError, SceneLine, ScenePackage
from open_shift.dialogue import (
    CHARACTER_PROFILES,
    DIALOGUE_SYSTEM_INSTRUCTION,
    MAX_DIALOGUE_CHARACTERS,
    PUBLIC_CHARACTER_IDENTITIES,
    DialogueLineDraft,
    DialogueTurnContext,
    DialogueUtterance,
    PlayerDialogueTurnContext,
    SceneDirection,
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
    ORIGINAL_CANON_FACTS,
    ORIGINAL_DIALOGUE_CHARACTER_LINE_COUNTS,
    ORIGINAL_DIALOGUE_CORPUS_FILE_COUNT,
    ORIGINAL_DIALOGUE_CORPUS_LINE_COUNT,
    ORIGINAL_DIALOGUE_CORPUS_SPECIAL_LINE_COUNT,
    ORIGINAL_DIALOGUE_CORPUS_SPEAKER_LABEL_COUNT,
    ORIGINAL_DIALOGUE_CORPUS_STOPLIP_RECORD_COUNT,
    ORIGINAL_DIALOGUE_STYLE,
    ORIGINAL_DIALOGUE_VOICE_STATS,
    ORIGINAL_DAILY_MIXING_MARKER_COUNT,
    ORIGINAL_DAILY_MUSIC_SELECTION_MARKER_COUNT,
    ORIGINAL_DAILY_SCENE_SHOW_MARKER_COUNT,
    ORIGINAL_DAILY_SCRIPT_FILE_COUNT,
    ORIGINAL_DAILY_SERVICE_RESULT_MARKER_COUNT,
    ORIGINAL_SHIFT_BEAT_SEQUENCE,
    SELECTED_TIMELINE_FACTS,
    CONTINUITY_FACTS,
    scene_direction_metadata,
    scene_direction_rules,
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


class SequenceTransport(FakeTransport):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(responses[0])
        self.responses = list(responses)

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        response = super().post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        self.response = self.responses[min(len(self.calls), len(self.responses) - 1)]
        return response


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
        anchor = self._anchor(context)
        return DialogueLineDraft(
            "neutral" if context.turn_index % 2 else "happy",
            f"{anchor}，这是{name}根据自己的记忆作出的第{context.turn_index + 1}次回应。",
        )

    def generate_player_dialogue_line(
        self, context: PlayerDialogueTurnContext
    ) -> DialogueLineDraft:
        self.player_contexts.append(context)
        if self.fail:
            raise RuntimeError("synthetic player dialogue failure")
        return DialogueLineDraft("neutral", f"{self._anchor(context)}？你接着说。")

    @staticmethod
    def _anchor(context: DialogueTurnContext | PlayerDialogueTurnContext) -> str:
        direction = context.scene_direction
        topic = direction.event_topic if direction is not None else ""
        match = re.search(r"[\u4e00-\u9fff]{2,}", topic)
        return match.group(0)[:12] if match else "今晚"


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
        self.assertTrue(ORIGINAL_CANON_FACTS)
        self.assertTrue(SELECTED_TIMELINE_FACTS)
        self.assertIn("已与 Gaby 和解", "".join(SELECTED_TIMELINE_FACTS))
        self.assertTrue(ORIGINAL_DIALOGUE_STYLE)
        self.assertEqual(
            {name for name, _ in ORIGINAL_DIALOGUE_VOICE_STATS},
            {"Jill", "Dana", "Alma", "Dorothy", "Sei", "Stella"},
        )
        self.assertEqual(ORIGINAL_DIALOGUE_CORPUS_FILE_COUNT, 28)
        self.assertEqual(ORIGINAL_DIALOGUE_CORPUS_LINE_COUNT, 17_194)
        self.assertEqual(ORIGINAL_DIALOGUE_CORPUS_SPECIAL_LINE_COUNT, 70)
        self.assertEqual(ORIGINAL_DIALOGUE_CORPUS_SPEAKER_LABEL_COUNT, 53)
        self.assertEqual(ORIGINAL_DIALOGUE_CORPUS_STOPLIP_RECORD_COUNT, 17_271)
        self.assertEqual(ORIGINAL_DAILY_SCRIPT_FILE_COUNT, 19)
        self.assertEqual(ORIGINAL_DAILY_MUSIC_SELECTION_MARKER_COUNT, 33)
        self.assertEqual(ORIGINAL_DAILY_MIXING_MARKER_COUNT, 620)
        self.assertEqual(ORIGINAL_DAILY_SERVICE_RESULT_MARKER_COUNT, 449)
        self.assertEqual(ORIGINAL_DAILY_SCENE_SHOW_MARKER_COUNT, 318)
        self.assertEqual(dict(ORIGINAL_DIALOGUE_CHARACTER_LINE_COUNTS)["Jill"], 7_036)
        self.assertNotIn("Classy", repr(CHARACTER_LORE["stella"]))

    def test_observation_contains_only_current_speaker_private_state(self) -> None:
        observation = dialogue_observation(turn_context())
        encoded = json.dumps(observation, ensure_ascii=False)
        self.assertIn("original_canon_facts", observation)
        self.assertIn("selected_timeline_facts", observation)
        self.assertIn("已与 Gaby 和解", "".join(observation["selected_timeline_facts"]))
        self.assertEqual(observation["speaker"]["agent_id"], "dana")
        self.assertIn("stable_core", observation["speaker"]["canon"])
        self.assertIn("voice", observation["speaker"]["canon"])
        self.assertIn("speech_cadence", observation["speaker"]["canon"])
        self.assertIn("interaction_patterns", observation["speaker"]["canon"])
        self.assertIn("drink_preferences", observation["speaker"]["canon"])
        self.assertIn("decision_principles", observation["speaker"]["canon"])
        self.assertIn("behavioral_boundaries", observation["speaker"]["canon"])
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
        self.assertIn("一个反应节拍", DIALOGUE_SYSTEM_INSTRUCTION)
        self.assertIn("speech_cadence", DIALOGUE_SYSTEM_INSTRUCTION)
        self.assertIn("interaction_patterns", DIALOGUE_SYSTEM_INSTRUCTION)
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
        self.assertIn("original_dialogue_voice_stats", observation)
        self.assertIn("original_shift_beat_sequence", observation)
        direction = observation["scene"]["scene_direction"]
        self.assertEqual(direction["scene_type"], "arrival_order")
        self.assertIn("current_beat", direction)
        self.assertIn("topic", direction)
        self.assertIn("unresolved_threads", direction)
        self.assertIn("我先找个位置坐", direction["avoid_patterns"])
        self.assertEqual(
            len(observation["original_shift_beat_sequence"]),
            len(ORIGINAL_SHIFT_BEAT_SEQUENCE),
        )
        self.assertAlmostEqual(
            observation["original_dialogue_voice_stats"]["mean_characters"],
            16.88,
        )
        self.assertNotIn("money", observation["speaker"])
        self.assertNotIn("world_tick", observation["scene"])
        self.assertNotIn("world_day", observation["scene"])
        self.assertNotIn("target_value", encoded)
        self.assertNotIn("secret_location", encoded)
        self.assertNotIn("private_mood", encoded)
        self.assertNotIn("999", encoded)

    def test_character_lore_exposes_action_boundaries_for_every_agent(self) -> None:
        for agent_id, lore in CHARACTER_LORE.items():
            payload = lore.prompt_payload()
            self.assertGreaterEqual(len(payload["decision_principles"]), 2)
            self.assertGreaterEqual(len(payload["behavioral_boundaries"]), 2)
            self.assertGreaterEqual(len(payload["speech_cadence"]), 2)
            self.assertGreaterEqual(len(payload["interaction_patterns"]), 2)
            self.assertTrue(all(item for item in payload["decision_principles"]))
            self.assertTrue(all(item for item in payload["behavioral_boundaries"]))
            self.assertTrue(all(item for item in payload["speech_cadence"]))
            self.assertTrue(all(item for item in payload["interaction_patterns"]))

    def test_arrival_direction_allows_description_based_drink_requests(self) -> None:
        rules = scene_direction_rules("arrival_order")
        self.assertTrue(any("甜度" in rule and "冰量" in rule for rule in rules))
        self.assertTrue(any("可执行" in rule for rule in rules))

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

    def test_output_validation_rejects_long_verbatim_repeats(self) -> None:
        prior = DialogueUtterance("dana", "这件事我还得再想想，不能现在决定。")
        context = DialogueTurnContext(
            "world_event_1",
            2,
            4,
            "Dorothy 在吧台谈起了一件还没有决定的事。",
            turn_context().speaker,
            ("dana", "dorothy"),
            (prior, prior),
        )
        with self.assertRaisesRegex(ValueError, "repeated"):
            validate_dialogue_output(
                {"expression_id": "neutral", "text": prior.text}, context
            )

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

    def test_dana_cannot_address_herself_as_the_boss(self) -> None:
        with self.assertRaisesRegex(ValueError, "boss"):
            validate_dialogue_output(
                {"expression_id": "neutral", "text": "老板，我自己会去处理。"},
                turn_context(),
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
        self.assertIn("speech_cadence", observation["speaker"]["canon"])
        self.assertIn("original_dialogue_voice_stats", observation)
        self.assertIn("scene_direction", observation["scene"])
        self.assertAlmostEqual(
            observation["original_dialogue_voice_stats"]["mean_characters"],
            13.73,
        )
        self.assertIn("interaction_patterns", observation["speaker"]["canon"])
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

    def test_explicit_scene_direction_survives_in_agent_observation(self) -> None:
        direction = SceneDirection(
            "service_reaction",
            "回扣：把饮品结果接回客人刚才提到的工作",
            "客人点的饮品已经准确完成，但刚才的工作麻烦还没说完。",
            "熟人之间可以直接吐槽，不需要互相安慰",
            ("工作麻烦还没有结论",),
            ("欢迎光临", "请稍等"),
            ("从具体结果继续，而不是总结整场对话",),
        )
        context = DialogueTurnContext(
            "order_result_1",
            1,
            3,
            "客人点的饮品已经准确完成。",
            private_context(),
            ("dana", "dorothy"),
            (DialogueUtterance("dorothy", "这杯闻起来还行。"),),
            None,
            direction,
        )
        payload = dialogue_observation(context)
        self.assertEqual(payload["scene"]["scene_direction"], direction.to_payload())

    def test_scene_direction_carries_original_shift_and_music_semantics(self) -> None:
        self.assertIn("围绕当天具体的城市事件或人物延续话题", ORIGINAL_SHIFT_BEAT_SEQUENCE[0])
        self.assertNotIn("处理吧台、库存或卫生", ORIGINAL_SHIFT_BEAT_SEQUENCE[0])
        pre_opening = scene_direction_metadata("pre_opening")
        self.assertEqual(pre_opening["shift_phase"], "pre_opening")
        self.assertEqual(pre_opening["music_policy"], "select_before_opening")
        self.assertEqual(
            pre_opening["break_save"],
            "not_applicable",
        )
        first_half = scene_direction_metadata("arrival_order")
        self.assertEqual(
            first_half["music_policy"],
            "continue_selected_shift_music",
        )
        second_half = scene_direction_metadata("second_half")
        self.assertEqual(second_half["shift_phase"], "second_half")
        self.assertEqual(second_half["music_policy"], "reuse_playlist_after_break")
        self.assertEqual(second_half["break_save"], "resume_after_native_save")

        direction = WorldSceneService._scene_direction(
            "service_reaction",
            "休息后接回上一轮没有说完的话题",
            "中场存档页已经关闭，下一位客人开始点单。",
            shift_phase="second_half",
        )
        payload = direction.to_payload()
        self.assertEqual(payload["shift_phase"], "second_half")
        self.assertEqual(payload["music_policy"], "reuse_playlist_after_break")
        self.assertEqual(payload["break_save"], "resume_after_native_save")

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
        self.assertEqual(call["payload"]["max_tokens"], 1024)
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

    def test_byok_dialogue_retries_one_malformed_json_response(self) -> None:
        transport = SequenceTransport(
            [
                {"choices": [{"message": {"content": "not json"}}]},
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "expression_id": "neutral",
                                        "text": "先把今天的事情说清楚。",
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            ]
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                max_calls=2,
            ),
            _api_key="secret",
            transport=transport,
        )

        result = provider.generate_dialogue_line(turn_context())

        self.assertEqual(result.text, "先把今天的事情说清楚。")
        self.assertEqual(provider.calls_used, 2)
        self.assertEqual(len(transport.calls), 2)

    def test_byok_dialogue_validation_does_not_echo_model_text_or_keys(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "expression_id": "neutral",
                                    "text": "模型秘密不应出现在异常里。",
                                    "api_key": "provider-secret",
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
                max_calls=1,
            ),
            _api_key="secret",
            transport=transport,
        )

        with self.assertRaises(BYOKValidationError) as caught:
            provider.generate_dialogue_line(turn_context())
        message = str(caught.exception)
        self.assertNotIn("api_key", message)
        self.assertNotIn("provider-secret", message)
        self.assertNotIn("模型秘密", message)

    def test_thinking_dialogue_retries_once_without_reasoning(self) -> None:
        transport = SequenceTransport(
            [
                {"choices": [{"message": {"content": "truncated"}}]},
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "expression_id": "neutral",
                                        "text": "那就先把这杯放在这里。",
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            ]
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                max_calls=2,
                thinking_mode=ThinkingMode.ENABLED,
            ),
            _api_key="secret",
            transport=transport,
        )

        result = provider.generate_dialogue_line(turn_context())

        self.assertEqual(result.text, "那就先把这杯放在这里。")
        self.assertEqual(
            transport.calls[0]["payload"]["thinking"], {"type": "enabled"}
        )
        self.assertEqual(
            transport.calls[1]["payload"]["thinking"], {"type": "disabled"}
        )

    def test_byok_player_dialogue_retries_one_invalid_contract_response(self) -> None:
        transport = SequenceTransport(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"expression_id": "happy", "text": "知道了。"},
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "expression_id": "neutral",
                                        "text": "知道了。范围还挺宽。",
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            ]
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                max_calls=2,
            ),
            _api_key="secret",
            transport=transport,
        )

        result = provider.generate_player_dialogue_line(player_turn_context())

        self.assertEqual(result.expression_id, "neutral")
        self.assertEqual(provider.calls_used, 2)


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
            self.assertEqual(len(scene.lines), 6)
            self.assertEqual(len(provider.dialogue_contexts), 2)
            self.assertEqual(len(provider.player_contexts), 3)
            self.assertIn("jill", {line.speaker_id for line in scene.lines})
            jill_line = next(line for line in scene.lines if line.speaker_id == "jill")
            self.assertIsNone(jill_line.portrait_id)
            self.assertIsNotNone(scene.order)
            context = provider.dialogue_contexts[-1]
            self.assertEqual(len(context.transcript), context.turn_index)
            self.assertEqual(context.turn_index, 2)
            self.assertEqual(context.turn_count, 6)
            self.assertEqual(context.speaker.actor.agent_id, scene.order.customer_id)
            assert context.scene_direction is not None
            self.assertIn("透露事件对自己", context.scene_direction.beat)
            self.assertEqual(
                [item.speaker_id for item in context.transcript],
                [scene.order.customer_id, "jill"],
            )
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
                    self.assertGreaterEqual(len(learned), 1)
                    self.assertTrue({item["source_type"] for item in learned}.issubset({"direct", "heard"}))
                    self.assertTrue(any("视角" in item["summary"] for item in learned))
                    next_context = SimulationEngine(
                        store, MockProvider()
                    ).context_for_agent(store.current_tick, participant_id)
                    self.assertTrue(
                        any("公开对话" in memory.summary for memory in next_context.memories)
                    )

                with WorldStore(db_path) as store:
                    # A silent participant named by the source event hears the
                    # scene, while an unrelated agent remains unaware.
                    event = store.append_event(
                        store.current_tick, "dialogue_context", "dana", "dorothy",
                        payload={"participants": ["dana", "dorothy", "alma"]},
                    )
                    scene_with_silent = ScenePackage(
                        "dialogue_silent_participant",
                        (
                            SceneLine("dialogue_silent_1", "dana", "sprite_dana", "neutral", "Dana 的话。"),
                            SceneLine("dialogue_silent_2", "jill", None, "neutral", "Jill 的回应。"),
                        ),
                    )
                    WorldSceneService._remember_generated_dialogue(store, scene_with_silent, event)
                    alma_memories = [
                        m
                        for m in store.list_memories("alma")
                        if m["canonical_key"]
                        == "dialogue:dialogue_silent_participant:alma:heard"
                    ]
                    dorothy_memories = [
                        m
                        for m in store.list_memories("dorothy")
                        if m["canonical_key"]
                        == "dialogue:dialogue_silent_participant:dorothy:heard"
                    ]
                    self.assertTrue(any(m["source_type"] == "heard" for m in alma_memories))
                    self.assertTrue(any(m["source_type"] == "heard" for m in dorothy_memories))
                    self.assertEqual(
                        [
                            m
                            for m in store.list_memories("stella")
                            if (m["canonical_key"] or "").startswith(
                                "dialogue:dialogue_silent_participant:"
                            )
                        ],
                        [],
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
            self.assertEqual(len(provider.dialogue_contexts), 1)
            self.assertEqual(len(provider.player_contexts), 0)
            second = service.open_scene(request)
            self.assertEqual(second, first)
            self.assertEqual(len(provider.dialogue_contexts), 1)
            self.assertEqual(len(provider.player_contexts), 0)
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
                self.assertEqual(len(dialogue_events), 1)
                self.assertEqual(
                    dialogue_events[0]["payload"]["scene_id"], first.scene_id
                )

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
            self.assertEqual(len(resolution.scene.lines), 4)
            self.assertEqual(len(provider.dialogue_contexts), 4)
            self.assertEqual(len(provider.player_contexts), 4)
            closing_context = provider.dialogue_contexts[-1]
            self.assertEqual(closing_context.turn_index, 2)
            self.assertEqual(closing_context.turn_count, 3)
            self.assertEqual(len(closing_context.transcript), 2)
            assert closing_context.scene_direction is not None
            self.assertIn("回到事件的未解决问题", closing_context.scene_direction.beat)
            self.assertNotIn("刚才那件事", "\n".join(line.text for line in resolution.scene.lines))
            self.assertNotIn("话题不用跟着杯子", "\n".join(line.text for line in resolution.scene.lines))
            self.assertNotIn("下一轮再接着说", "\n".join(line.text for line in resolution.scene.lines))
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
