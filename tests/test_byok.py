from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from open_shift.byok import (
    APIProtocol,
    BYOKBudgetExceeded,
    BYOKConfig,
    BYOKConfigurationError,
    BYOKProvider,
    BYOKResponseError,
    BYOKValidationError,
    ResponseFormat,
    validate_action_output,
)
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
            BYOKConfig("https://api.example.test/v1", "test-model"),
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

    def test_budget_stops_before_a_second_transport_call(self) -> None:
        transport = FakeTransport({"output_text": action_json()})
        provider = BYOKProvider(
            BYOKConfig("https://api.example.test/v1", "test-model", max_calls=1),
            _api_key="secret",
            transport=transport,
        )
        provider.decide(context())
        with self.assertRaises(BYOKBudgetExceeded):
            provider.decide(context())
        self.assertEqual(len(transport.calls), 1)

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
            BYOKConfig("https://api.example.test/v1", "test-model"),
            _api_key="secret",
            transport=FakeTransport({"output_text": "```json\n{}\n```"}),
        )
        with self.assertRaises(BYOKResponseError):
            provider.decide(context())

    def test_remote_http_is_rejected_but_loopback_is_allowed(self) -> None:
        with self.assertRaises(BYOKConfigurationError):
            BYOKConfig("http://api.example.test/v1", "test-model")
        local = BYOKConfig("http://127.0.0.1:8000/v1", "test-model")
        self.assertEqual(local.endpoint, "http://127.0.0.1:8000/v1/responses")

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
