from __future__ import annotations

import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from open_shift.byok import (
    APIProtocol,
    BYOKBudgetExceeded,
    BYOKConfig,
    BYOKConfigurationError,
    BYOKProvider,
    BYOKResponseError,
    BYOKValidationError,
    ResponseFormat,
    ThinkingMode,
    normalize_json_object_output,
    validate_action_output,
)
from open_shift.cli import _provider_factory
from open_shift.models import AgentState, DecisionContext, Goal, Relationship
from open_shift.providers import MockProvider
from open_shift.scenario import create_demo_world
from open_shift.store import WorldStore


def context() -> DecisionContext:
    dana = AgentState("dana", "Dana", "home", 90, 0.2, "steady", 480)
    dorothy = AgentState("dorothy", "Dorothy", "work", 25, 0.3, "playful", 600)
    return DecisionContext(
        tick=480,
        seed=7,
        actor=dana,
        agents=(dana, dorothy),
        relationships=(Relationship("dana", "dorothy", 0.2, 0.3),),
        goals=(Goal("save", "dana", "savings", None, 150, 0.7),),
        locations=("home", "work", "va11_hall_a"),
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


def action_json(**overrides: Any) -> str:
    value: dict[str, Any] = {
        "action_type": "message",
        "target_id": "dorothy",
        "location": None,
        "duration_minutes": 0,
        "reason_code": "maintain_relationship",
    }
    value.update(overrides)
    return json.dumps(value)


class BYOKProviderTests(unittest.TestCase):
    def test_missing_optional_key_keeps_local_bridge_playable(self) -> None:
        args = Namespace(
            provider_base_url="https://api.deepseek.com",
            provider_model="deepseek-v4-flash",
            provider_protocol=APIProtocol.CHAT_COMPLETIONS.value,
            provider_response_format=ResponseFormat.JSON_OBJECT.value,
            provider_timeout=30.0,
            provider_api_key_env="OPEN_SHIFT_KEY_THAT_IS_NOT_SET",
            provider_max_calls=100,
            provider_thinking=ThinkingMode.DISABLED.value,
            provider_required=False,
        )
        with patch.dict("os.environ", {}, clear=True):
            factory = _provider_factory(args)
        self.assertIsInstance(factory(), MockProvider)

    def test_deepseek_v4_flash_defaults_disable_thinking(self) -> None:
        args = Namespace(
            provider_base_url="https://api.deepseek.com",
            provider_model=None,
            provider_protocol=None,
            provider_response_format=None,
            provider_timeout=30.0,
            provider_api_key_env="OPEN_SHIFT_API_KEY",
            provider_max_calls=100,
            provider_thinking=ThinkingMode.DEFAULT.value,
            provider_required=False,
        )
        sentinel = object()
        with patch.object(BYOKProvider, "from_env", return_value=sentinel) as from_env:
            factory = _provider_factory(args)
        self.assertIsNotNone(factory)
        assert factory is not None
        self.assertIs(factory(), sentinel)
        config = from_env.call_args.args[0]
        self.assertEqual(config.model, "deepseek-v4-flash")
        self.assertIs(config.thinking_mode, ThinkingMode.DISABLED)

    def test_required_provider_rejects_a_missing_key(self) -> None:
        args = Namespace(
            provider_base_url="https://api.deepseek.com",
            provider_model="deepseek-v4-flash",
            provider_protocol=APIProtocol.CHAT_COMPLETIONS.value,
            provider_response_format=ResponseFormat.JSON_OBJECT.value,
            provider_timeout=30.0,
            provider_api_key_env="OPEN_SHIFT_KEY_THAT_IS_NOT_SET",
            provider_max_calls=100,
            provider_thinking=ThinkingMode.DISABLED.value,
            provider_required=True,
        )
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(BYOKConfigurationError):
                _provider_factory(args)

    def test_responses_protocol_builds_schema_request_and_parses_action(self) -> None:
        transport = FakeTransport(
            {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": action_json()}
                        ]
                    }
                ]
            }
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.RESPONSES,
                response_format=ResponseFormat.JSON_SCHEMA,
            ),
            _api_key="super-secret",
            transport=transport,
        )
        action = provider.decide(context())

        self.assertEqual(action.action_type.value, "message")
        self.assertEqual(action.target_id, "dorothy")
        self.assertEqual(provider.calls_used, 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.example.test/v1/responses")
        self.assertEqual(call["headers"]["Authorization"], "Bearer super-secret")
        self.assertEqual(
            call["payload"]["text"]["format"]["type"], "json_schema"
        )
        self.assertNotIn("super-secret", repr(provider))

    def test_chat_completions_protocol_is_isolated(self) -> None:
        transport = FakeTransport(
            {"choices": [{"message": {"content": action_json(action_type="rest", target_id=None, reason_code="need_rest")}}]}
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.CHAT_COMPLETIONS,
            ),
            _api_key="secret",
            transport=transport,
        )
        action = provider.decide(context())
        self.assertEqual(action.action_type.value, "rest")
        call = transport.calls[0]
        self.assertEqual(
            call["url"], "https://api.example.test/v1/chat/completions"
        )
        self.assertIn("response_format", call["payload"])
        self.assertNotIn("text", call["payload"])
        self.assertNotIn("thinking", call["payload"])

    def test_chat_thinking_can_be_explicitly_disabled(self) -> None:
        transport = FakeTransport(
            {"choices": [{"message": {"content": action_json()}}]}
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "deepseek-v4-flash",
                protocol=APIProtocol.CHAT_COMPLETIONS,
                thinking_mode=ThinkingMode.DISABLED,
            ),
            _api_key="secret",
            transport=transport,
        )
        provider.decide(context())
        self.assertEqual(
            transport.calls[0]["payload"]["thinking"], {"type": "disabled"}
        )

    def test_explicit_thinking_rejects_responses_protocol(self) -> None:
        with self.assertRaises(BYOKConfigurationError):
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.RESPONSES,
                thinking_mode=ThinkingMode.DISABLED,
            )

    def test_chat_json_object_mode_uses_compatible_response_format(self) -> None:
        transport = FakeTransport(
            {"choices": [{"message": {"content": action_json()}}]}
        )
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.CHAT_COMPLETIONS,
                response_format=ResponseFormat.JSON_OBJECT,
            ),
            _api_key="secret",
            transport=transport,
        )
        action = provider.decide(context())
        self.assertEqual(action.action_type.value, "message")
        self.assertEqual(
            transport.calls[0]["payload"]["response_format"],
            {"type": "json_object"},
        )
        self.assertNotIn("json_schema", transport.calls[0]["payload"])
        self.assertIn(
            '"reason_code":"earn_money"',
            transport.calls[0]["payload"]["messages"][0]["content"],
        )

    def test_json_object_mode_normalizes_optional_omissions(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action_type": "work",
                                    "reason_code": "earn_money",
                                }
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
            ),
            _api_key="secret",
            transport=transport,
        )
        action = provider.decide(context())
        self.assertEqual(action.action_type.value, "work")
        self.assertIsNone(action.target_id)
        self.assertIsNone(action.location)
        self.assertEqual(action.duration_minutes, 0)

    def test_json_object_mode_accepts_known_envelope_but_not_unknown_fields(self) -> None:
        normalized = normalize_json_object_output(
            {
                "action_proposal": {
                    "action_type": "rest",
                    "reason_code": "need_rest",
                }
            }
        )
        self.assertEqual(normalized["action_type"], "rest")
        self.assertIsNone(normalized["target_id"])
        with self.assertRaisesRegex(BYOKValidationError, "unknown fields: amount"):
            normalize_json_object_output(
                {
                    "action_type": "work",
                    "reason_code": "earn_money",
                    "amount": 999,
                }
            )

    def test_model_output_accepts_bounded_markdown_json_wrapper(self) -> None:
        from open_shift.byok import _as_action_object

        value = _as_action_object(
            "Here is the requested object:\n```json\n"
            '{"action_type":"work","target_id":null,"location":null,'
            '"duration_minutes":480,"reason_code":"earn_money"}\n```'
        )
        self.assertEqual(value["action_type"], "work")

    def test_model_output_ignores_thinking_block_before_final_json(self) -> None:
        from open_shift.byok import _as_action_object

        value = _as_action_object(
            '<think>考虑过这个示例：{"action_type":"rest"}</think>\n'
            '{"action_type":"work","target_id":null,"location":null,'
            '"duration_minutes":480,"reason_code":"earn_money"}'
        )
        self.assertEqual(value["action_type"], "work")

    def test_chat_output_accepts_structured_content_parts(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "output_text", "text": action_json()}
                            ],
                            "reasoning_content": "内部推理不应作为答案",
                        }
                    }
                ]
            }
        )
        provider = BYOKProvider(
            BYOKConfig("https://api.example.test/v1", "test-model"),
            _api_key="secret",
            transport=transport,
        )
        self.assertEqual(provider.decide(context()).action_type.value, "message")

    def test_chat_output_rejects_reasoning_without_final_content(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning_content": "我还在思考",
                        }
                    }
                ]
            }
        )
        provider = BYOKProvider(
            BYOKConfig("https://api.example.test/v1", "test-model"),
            _api_key="secret",
            transport=transport,
        )
        with self.assertRaisesRegex(BYOKResponseError, "final content"):
            provider.decide(context())

    def test_model_output_still_rejects_json_arrays(self) -> None:
        from open_shift.byok import BYOKResponseError, _as_action_object

        with self.assertRaises(BYOKResponseError):
            _as_action_object("[{}]")

    def test_budget_stops_before_a_second_transport_call(self) -> None:
        transport = FakeTransport({"output_text": action_json()})
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.RESPONSES,
                response_format=ResponseFormat.JSON_SCHEMA,
                max_calls=1,
            ),
            _api_key="secret",
            transport=transport,
        )
        provider.decide(context())
        with self.assertRaises(BYOKBudgetExceeded):
            provider.decide(context())
        self.assertEqual(len(transport.calls), 1)
        report = provider.budget_report()
        self.assertEqual(report["calls_used"], 1)
        self.assertEqual(report["calls_remaining"], 0)
        self.assertTrue(report["exhausted"])

    def test_invalid_or_extra_model_fields_are_rejected(self) -> None:
        value = json.loads(action_json())
        value["amount"] = 1
        with self.assertRaises(BYOKValidationError):
            validate_action_output(value, context())

    def test_invisible_target_and_unknown_location_are_rejected(self) -> None:
        with self.assertRaises(BYOKValidationError):
            validate_action_output(
                json.loads(action_json(target_id="unknown")), context()
            )
        with self.assertRaises(BYOKValidationError):
            validate_action_output(
                json.loads(
                    action_json(
                        action_type="travel",
                        target_id=None,
                        location="moon",
                        reason_code="go_moon",
                    )
                ),
                context(),
            )

    def test_non_json_output_is_rejected(self) -> None:
        provider = BYOKProvider(
            BYOKConfig(
                "https://api.example.test/v1",
                "test-model",
                protocol=APIProtocol.RESPONSES,
                response_format=ResponseFormat.JSON_SCHEMA,
            ),
            _api_key="secret",
            transport=FakeTransport({"output_text": "```json\n{}\n```"}),
        )
        with self.assertRaises(BYOKResponseError):
            provider.decide(context())

    def test_remote_http_is_rejected_but_loopback_is_allowed(self) -> None:
        with self.assertRaises(BYOKConfigurationError):
            BYOKConfig("http://api.example.test/v1", "test-model")
        local = BYOKConfig("http://127.0.0.1:8000/v1", "test-model")
        self.assertEqual(
            local.endpoint, "http://127.0.0.1:8000/v1/chat/completions"
        )

    def test_from_env_does_not_put_key_in_config(self) -> None:
        config = BYOKConfig(
            "https://api.example.test/v1",
            "test-model",
            api_key_env="OPEN_SHIFT_TEST_KEY",
        )
        old = os.environ.get("OPEN_SHIFT_TEST_KEY")
        os.environ["OPEN_SHIFT_TEST_KEY"] = "environment-secret"
        try:
            provider = BYOKProvider.from_env(
                config,
                transport=FakeTransport({"output_text": action_json()}),
            )
            self.assertNotIn("environment-secret", repr(config))
            self.assertNotIn("environment-secret", repr(provider))
        finally:
            if old is None:
                os.environ.pop("OPEN_SHIFT_TEST_KEY", None)
            else:
                os.environ["OPEN_SHIFT_TEST_KEY"] = old

    def test_engine_continues_when_provider_fails(self) -> None:
        class FailingProvider:
            def decide(self, decision_context: DecisionContext):
                raise BYOKResponseError("invalid output")

        with tempfile.TemporaryDirectory() as temp_dir:
            with WorldStore(Path(temp_dir) / "world.sqlite3") as store:
                report = create_demo_world(store, FailingProvider(), seed=7).run_days(1)
                self.assertGreater(report.provider_errors, 0)
                self.assertGreater(report.processed_turns, 0)
                self.assertIn("provider_error", report.event_counts)
                self.assertIn("worked", report.event_counts)


if __name__ == "__main__":
    unittest.main()
