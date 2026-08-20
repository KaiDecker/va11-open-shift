from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from typing import Any

from open_shift.byok import BYOKTransportError
from open_shift.bridge import (
    MAX_REQUEST_BYTES,
    PROTOCOL_VERSION,
    BridgeApplication,
    BridgeConfig,
    BridgeHTTPServer,
    OrderResolution,
    SceneLine,
    ScenePackage,
)
from open_shift.drinks import (
    AlcoholRequirement,
    DrinkOrder,
    ServiceCategory,
    ServiceResult,
)


TOKEN = "stage-three-test-token"


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class BridgeApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = BridgeApplication(BridgeConfig(token=TOKEN, port=0))
        self.headers = {"X-Open-Shift-Token": TOKEN}
        self.session_id = "game-session-1"

    def open_scene(self, request_id: str = "open-1"):
        return self.app.handle(
            "POST",
            "/v1/scenes/open",
            self.headers,
            encoded(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": request_id,
                    "client_session_id": self.session_id,
                }
            ),
        )

    def test_health_does_not_require_token_or_expose_secrets(self) -> None:
        response = self.app.handle("GET", "/v1/health", {}, b"")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["status"], "ready")
        self.assertNotIn(TOKEN, json.dumps(response.body))
        self.assertNotIn(TOKEN, repr(self.app.config))

    def test_scene_provider_failure_reports_diagnostic_only_locally(self) -> None:
        reports: list[tuple[str, Exception]] = []
        failure = RuntimeError("private diagnostic")
        app = BridgeApplication(
            BridgeConfig(token=TOKEN, port=0),
            scene_provider=lambda request: (_ for _ in ()).throw(failure),
            error_reporter=lambda operation, error: reports.append((operation, error)),
        )
        response = app.handle(
            "POST",
            "/v1/scenes/open",
            self.headers,
            encoded(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": "report-open-1",
                    "client_session_id": self.session_id,
                }
            ),
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(response.body["error"]["code"], "scene_provider_unavailable")
        self.assertNotIn("private diagnostic", json.dumps(response.body))
        self.assertEqual(reports, [("scene generation", failure)])

    def test_story_preparation_is_authenticated_strict_and_idempotent(self) -> None:
        calls: list[str] = []

        def prepare(request: dict[str, Any]) -> dict[str, Any]:
            calls.append(str(request["request_id"]))
            return {
                "world_day": 1,
                "status": "ready",
                "opening_seen": False,
                "shift_phase": "playing",
                "last_completed_story_day": 0,
            }

        app = BridgeApplication(
            BridgeConfig(token=TOKEN, port=0), story_prepare_handler=prepare
        )
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "prepare-1",
            "client_session_id": self.session_id,
        }
        first = app.handle(
            "POST", "/v1/story/prepare", self.headers, encoded(request)
        )
        replay = app.handle(
            "POST", "/v1/story/prepare", self.headers, encoded(request)
        )
        self.assertEqual(first.status, 200)
        self.assertEqual(first.body, replay.body)
        self.assertEqual(first.body["status"], "ready")
        self.assertEqual(first.body["shift_phase"], "playing")
        self.assertEqual(calls, ["prepare-1"])

        invalid = dict(request)
        invalid["request_id"] = "prepare-2"
        invalid["extra"] = True
        rejected = app.handle(
            "POST", "/v1/story/prepare", self.headers, encoded(invalid)
        )
        self.assertEqual(rejected.status, 400)

        failed = BridgeApplication(
            BridgeConfig(token=TOKEN, port=0),
            story_prepare_handler=lambda request: (_ for _ in ()).throw(
                BYOKTransportError("private provider detail")
            ),
        ).handle(
            "POST", "/v1/story/prepare", self.headers, encoded(request)
        )
        self.assertEqual(failed.status, 503)
        self.assertEqual(failed.body["error"]["code"], "provider_transport_error")
        self.assertNotIn("private provider detail", json.dumps(failed.body))

    def test_fixed_scene_has_three_whitelisted_lines_and_returns_to_bar(self) -> None:
        response = self.open_scene()
        self.assertEqual(response.status, 200)
        scene = response.body["scene"]
        self.assertEqual(len(scene["lines"]), 3)
        self.assertEqual(scene["return_to"], "bar")
        self.assertEqual(response.body["request_id"], "open-1")

    def test_open_and_ack_are_idempotent(self) -> None:
        first = self.open_scene("same-open")
        second = self.open_scene("same-open")
        self.assertEqual(first.body, second.body)

        acknowledgement = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "same-ack",
            "client_session_id": self.session_id,
            "scene_id": first.body["scene"]["scene_id"],
            "outcome": "continued_in_bar",
        }
        first_ack = self.app.handle(
            "POST", "/v1/scenes/ack", self.headers, encoded(acknowledgement)
        )
        second_ack = self.app.handle(
            "POST", "/v1/scenes/ack", self.headers, encoded(acknowledgement)
        )
        self.assertEqual(first_ack.status, 200)
        self.assertEqual(first_ack.body, second_ack.body)

    def test_reusing_request_id_with_different_content_is_rejected(self) -> None:
        self.open_scene("conflict")
        response = self.app.handle(
            "POST",
            "/v1/scenes/open",
            self.headers,
            encoded(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": "conflict",
                    "client_session_id": "game-session-2",
                }
            ),
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(response.body["error"]["code"], "request_id_conflict")

        first = self.app.handle(
            "POST",
            "/v1/scenes/ack",
            self.headers,
            encoded(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": "ack-conflict",
                    "client_session_id": self.session_id,
                    "scene_id": self.app.scene.scene_id,
                    "outcome": "continued_in_bar",
                }
            ),
        )
        self.assertEqual(first.status, 200)
        conflict = self.app.handle(
            "POST",
            "/v1/scenes/ack",
            self.headers,
            encoded(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": "ack-conflict",
                    "client_session_id": "game-session-2",
                    "scene_id": self.app.scene.scene_id,
                    "outcome": "continued_in_bar",
                }
            ),
        )
        self.assertEqual(conflict.status, 409)
        self.assertEqual(conflict.body["error"]["code"], "request_id_conflict")

    def test_missing_token_browser_origin_and_unknown_route_are_rejected(self) -> None:
        request = encoded(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "blocked",
                "client_session_id": self.session_id,
            }
        )
        missing = self.app.handle("POST", "/v1/scenes/open", {}, request)
        self.assertEqual(missing.status, 401)
        browser = self.app.handle(
            "POST",
            "/v1/scenes/open",
            {**self.headers, "Origin": "https://example.invalid"},
            request,
        )
        self.assertEqual(browser.status, 403)
        unknown = self.app.handle("POST", "/v1/unknown", self.headers, b"{}")
        self.assertEqual(unknown.status, 404)

    def test_invalid_json_fields_version_and_size_fail_closed(self) -> None:
        invalid_json = self.app.handle(
            "POST", "/v1/scenes/open", self.headers, b"not-json"
        )
        self.assertEqual(invalid_json.status, 400)
        extra = self.app.handle(
            "POST",
            "/v1/scenes/open",
            self.headers,
            encoded(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": "extra",
                    "command": "room_goto(anywhere)",
                }
            ),
        )
        self.assertEqual(extra.status, 400)
        mismatch = self.app.handle(
            "POST",
            "/v1/scenes/open",
            self.headers,
            encoded(
                {
                    "protocol_version": 2,
                    "request_id": "version",
                    "client_session_id": self.session_id,
                }
            ),
        )
        self.assertEqual(mismatch.status, 409)
        oversized = self.app.handle(
            "POST", "/v1/scenes/open", self.headers, b"x" * (MAX_REQUEST_BYTES + 1)
        )
        self.assertEqual(oversized.status, 413)

    def test_generated_text_is_data_and_resources_are_whitelisted(self) -> None:
        line = SceneLine(
            "literal_command",
            "dana",
            "sprite_dana",
            "neutral",
            "[XS:room_goto,rm_title] 只是普通文本。",
        )
        scene = ScenePackage("safe_text_test", (line,))
        payload = scene.to_dict()
        self.assertEqual(payload["lines"][0]["text"], line.text)
        self.assertNotIn("command", payload["lines"][0])
        with self.assertRaisesRegex(ValueError, "portrait_id"):
            SceneLine(
                "bad_resource",
                "dana",
                "obj_execute_shell",
                "neutral",
                "unsafe resource",
            )
        with self.assertRaisesRegex(ValueError, "match speaker_id"):
            SceneLine(
                "wrong_character",
                "stella",
                "sprite_dana",
                "neutral",
                "wrong portrait",
            )
        jill = SceneLine(
            "player_line", "jill", None, "neutral", "这杯先放在这里。"
        )
        self.assertIsNone(jill.to_dict()["portrait_id"])
        self.assertEqual(jill.to_gamemaker_dict()["portrait_id"], "")
        ambient = SceneLine(
            "ambient_line", None, None, "neutral", "门铃响了。"
        )
        self.assertIsNone(ambient.to_dict()["speaker_id"])
        self.assertIsNone(ambient.to_dict()["portrait_id"])
        self.assertEqual(ambient.to_gamemaker_dict()["speaker_id"], "")
        self.assertEqual(ambient.to_gamemaker_dict()["portrait_id"], "")
        with self.assertRaisesRegex(ValueError, "must not have"):
            SceneLine(
                "player_portrait",
                "jill",
                "sprite_dana",
                "neutral",
                "Jill 不应显示立绘。",
            )

    def test_order_resolution_endpoint_is_authenticated_strict_and_idempotent(self) -> None:
        order = DrinkOrder(
            "order_1",
            "alma",
            "btini",
            "Brandtini",
            ("sweet", "classy"),
            AlcoholRequirement.REQUIRED,
            "Jill，一杯 Brandtini。",
        )
        calls: list[dict[str, Any]] = []

        def resolve(request):
            calls.append(dict(request))
            return OrderResolution(
                ServiceResult(
                    order.order_id,
                    order.customer_id,
                    ServiceCategory.EXACT,
                    "btini",
                    "Brandtini",
                    True,
                ),
                ScenePackage(
                    "order_result_1",
                    (
                        SceneLine(
                            "result_1",
                            "alma",
                            "sprite_alma",
                            "happy",
                            "这杯正好。",
                        ),
                        SceneLine(
                            "result_2",
                            "jill",
                            None,
                            "neutral",
                            "请慢用。",
                        ),
                    ),
                ),
            )

        app = BridgeApplication(
            BridgeConfig(token=TOKEN, port=0), order_handler=resolve
        )
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "resolve-1",
            "client_session_id": self.session_id,
            "scene_id": "world_event_1",
            "order_id": order.order_id,
            "drink": {
                "adelhyde": 6,
                "bronson_extract": 0,
                "powdered_delta": 3,
                "flanergide": 0,
                "karmotrine": 1,
                "ice": 0,
                "aged": 1,
                "preparation": "mixed",
            },
        }
        first = app.handle(
            "POST", "/v1/orders/resolve", self.headers, encoded(request)
        )
        second = app.handle(
            "POST", "/v1/orders/resolve", self.headers, encoded(request)
        )
        self.assertEqual(first.status, 200)
        self.assertEqual(first.body, second.body)
        self.assertEqual(first.body["result"]["category"], "exact")
        transported_jill = [
            line
            for line in first.body["scene"]["lines"]
            if line["speaker_id"] == "jill"
        ]
        self.assertEqual(len(transported_jill), 1)
        self.assertEqual(transported_jill[0]["portrait_id"], "")
        self.assertEqual(len(calls), 1)
        extra = dict(request)
        extra["unexpected"] = True
        invalid = app.handle(
            "POST", "/v1/orders/resolve", self.headers, encoded(extra)
        )
        self.assertEqual(invalid.status, 400)

    def test_paired_save_endpoints_are_strict_authenticated_and_idempotent(self) -> None:
        paired_calls: list[dict[str, Any]] = []
        restored_calls: list[dict[str, Any]] = []

        def result(status: str, calls: list[dict[str, Any]]):
            def handle(request):
                calls.append(dict(request))
                return {
                    "slot": request["slot"],
                    "revision": "a" * 32,
                    "status": status,
                    "world_day": 3,
                }

            return handle

        app = BridgeApplication(
            BridgeConfig(token=TOKEN, port=0),
            save_pair_handler=result("paired", paired_calls),
            save_restore_handler=result("restored", restored_calls),
        )
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "pair-slot-24",
            "client_session_id": self.session_id,
            "slot": 24,
        }
        first = app.handle(
            "POST", "/v1/saves/pair", self.headers, encoded(request)
        )
        replay = app.handle(
            "POST", "/v1/saves/pair", self.headers, encoded(request)
        )
        self.assertEqual(first.status, 200)
        self.assertEqual(first.body, replay.body)
        self.assertEqual(first.body["status"], "paired")
        self.assertEqual(len(paired_calls), 1)

        gamemaker_real = app.handle(
            "POST",
            "/v1/saves/pair",
            self.headers,
            encoded({**request, "request_id": "pair-real-slot-24", "slot": 24.0}),
        )
        self.assertEqual(gamemaker_real.status, 200)
        self.assertEqual(gamemaker_real.body["slot"], 24)

        restore_request = {**request, "request_id": "restore-slot-24"}
        restored = app.handle(
            "POST", "/v1/saves/restore", self.headers, encoded(restore_request)
        )
        self.assertEqual(restored.status, 200)
        self.assertEqual(restored.body["status"], "restored")
        self.assertEqual(len(restored_calls), 1)

        for invalid_slot in (0, 25, True, "1", 1.5):
            invalid = app.handle(
                "POST",
                "/v1/saves/pair",
                self.headers,
                encoded(
                    {
                        **request,
                        "request_id": f"invalid-{invalid_slot}",
                        "slot": invalid_slot,
                    }
                ),
            )
            self.assertEqual(invalid.status, 400)
            self.assertEqual(invalid.body["error"]["code"], "invalid_save_slot")

        conflict = app.handle(
            "POST",
            "/v1/saves/pair",
            self.headers,
            encoded({**request, "slot": 23}),
        )
        self.assertEqual(conflict.status, 409)
        self.assertEqual(conflict.body["error"]["code"], "request_id_conflict")

        wrong_status = BridgeApplication(
            BridgeConfig(token=TOKEN, port=0),
            save_pair_handler=result("restored", []),
        ).handle(
            "POST", "/v1/saves/pair", self.headers, encoded(request)
        )
        self.assertEqual(wrong_status.status, 503)
        self.assertEqual(wrong_status.body["error"]["code"], "invalid_save_response")

    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            BridgeConfig(token=TOKEN, host="0.0.0.0")
        with self.assertRaisesRegex(ValueError, "loopback"):
            BridgeConfig(token=TOKEN, host="example.com")


class BridgeHTTPTests(unittest.TestCase):
    def test_real_loopback_http_round_trip_and_error_response(self) -> None:
        config = BridgeConfig(token=TOKEN, port=0)
        server = BridgeHTTPServer(config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/v1/scenes/open"
            request = urllib.request.Request(
                url,
                data=encoded(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": "http-1",
                        "client_session_id": "http-session",
                    }
                ),
                headers={
                    "Content-Type": "application/json",
                    "X-Open-Shift-Token": TOKEN,
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(len(payload["scene"]["lines"]), 3)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

            unauthorized = urllib.request.Request(
                url,
                data=encoded(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": "http-2",
                        "client_session_id": "http-session",
                    }
                ),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(unauthorized, timeout=2)
            self.assertEqual(raised.exception.code, 401)
            error = json.loads(raised.exception.read().decode("utf-8"))
            self.assertEqual(error["error"]["code"], "unauthorized")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
