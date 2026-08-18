from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from open_shift.bridge import (
    PROTOCOL_VERSION,
    BridgeApplication,
    BridgeConfig,
    BridgeHTTPServer,
)
from open_shift.launcher import LaunchConfig, RuntimeSession
from open_shift.cli import _paired_save_response
from open_shift.paired_saves import PairedSaveManager
from open_shift.providers import MockProvider
from open_shift.store import WorldStore
from open_shift.world_bridge import WorldSceneService


TOKEN = "stage-four-runtime-token"


class Stage4WorldBridgeTests(unittest.TestCase):
    def test_real_http_pairs_and_restores_original_slot_with_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "world.sqlite3"
            native_dir = root / "native" / "saves"
            native_dir.mkdir(parents=True)
            native_save = native_dir / "Record of Waifu Wars[6].txt"
            native_save.write_text("17/8/2026 20:00:00\n1\n1001\n", encoding="utf-8")
            with WorldStore(db_path) as store:
                store.set_meta("current_story_day", 2)
                store.set_meta("last_completed_story_day", 1)
                store.set_meta("shift_phase", "save_required")
                store.set_current_tick(1440)
            world = WorldSceneService(
                db_path,
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            manager = PairedSaveManager(db_path, native_dir, root / "paired")
            config = BridgeConfig(token=TOKEN, port=0)
            app = BridgeApplication(
                config,
                save_pair_handler=lambda request: _paired_save_response(
                    world, manager, dict(request), "paired"
                ),
                save_restore_handler=lambda request: _paired_save_response(
                    world, manager, dict(request), "restored"
                ),
            )
            server = BridgeHTTPServer(config, app=app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                headers = {
                    "Content-Type": "application/json",
                    "X-Open-Shift-Token": TOKEN,
                }

                def post(path: str, payload: dict[str, object]):
                    request = Request(
                        base + path,
                        data=json.dumps(payload).encode(),
                        headers=headers,
                        method="POST",
                    )
                    with urlopen(request, timeout=2) as response:
                        return json.loads(response.read().decode())

                pair_request = {
                    "protocol_version": 1,
                    "request_id": "http-pair-slot-6",
                    "client_session_id": "http-save-session-0001",
                    "slot": 6.0,
                }
                paired = post("/v1/saves/pair", pair_request)
                self.assertEqual(paired["status"], "paired")
                self.assertEqual(paired["world_day"], 2)
                with WorldStore(db_path) as store:
                    self.assertEqual(store.get_meta("shift_phase"), "playing")
                    store.set_meta("current_story_day", 9)
                    store.set_current_tick(9999)

                restored = post(
                    "/v1/saves/restore",
                    {**pair_request, "request_id": "http-restore-slot-6"},
                )
                self.assertEqual(restored["status"], "restored")
                self.assertEqual(restored["revision"], paired["revision"])
                with WorldStore(db_path) as store:
                    self.assertEqual(store.get_meta("current_story_day"), "2")
                    self.assertEqual(store.get_meta("shift_phase"), "playing")
                    self.assertEqual(store.current_tick, 1440)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_open_advances_world_and_ack_is_persisted_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            world = WorldSceneService(db_path, advance_minutes=60)
            config = BridgeConfig(token=TOKEN, port=0)
            app = BridgeApplication(
                config,
                scene_provider=world.open_scene,
                ack_handler=world.ack_scene,
            )
            headers = {"X-Open-Shift-Token": TOKEN}
            body = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "world-open-1",
                "client_session_id": "session-1",
            }
            first = app.handle("POST", "/v1/scenes/open", headers, json.dumps(body).encode())
            second = app.handle("POST", "/v1/scenes/open", headers, json.dumps(body).encode())
            self.assertEqual(first.status, 200)
            self.assertEqual(first.body, second.body)
            self.assertEqual(first.body["scene"]["scene_id"].startswith("world_event_"), True)
            with WorldStore(db_path) as store:
                self.assertEqual(store.current_tick, 60)
            ack = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "world-ack-1",
                "client_session_id": "session-1",
                "scene_id": first.body["scene"]["scene_id"],
                "outcome": "order_started",
            }
            ack_body = json.dumps(ack).encode()
            self.assertEqual(app.handle("POST", "/v1/scenes/ack", headers, ack_body).status, 200)
            self.assertEqual(app.handle("POST", "/v1/scenes/ack", headers, ack_body).status, 200)
            with WorldStore(db_path) as store:
                self.assertEqual(
                    len([e for e in store.list_events() if e["event_type"] == "player_scene_ack"]),
                    1,
                )

            restarted_world = WorldSceneService(db_path, advance_minutes=60)
            restarted_app = BridgeApplication(
                config,
                scene_provider=restarted_world.open_scene,
                ack_handler=restarted_world.ack_scene,
            )
            replay = restarted_app.handle(
                "POST", "/v1/scenes/open", headers, json.dumps(body).encode()
            )
            self.assertEqual(replay.body, first.body)
            conflicting_body = {**body, "client_session_id": "different-session"}
            conflict = restarted_app.handle(
                "POST", "/v1/scenes/open", headers, json.dumps(conflicting_body).encode()
            )
            self.assertEqual(conflict.status, 409)
            replay_ack = restarted_app.handle(
                "POST", "/v1/scenes/ack", headers, ack_body
            )
            self.assertEqual(replay_ack.status, 200)
            with WorldStore(db_path) as store:
                self.assertEqual(store.current_tick, 60)
                self.assertEqual(
                    len([e for e in store.list_events() if e["event_type"] == "player_scene_ack"]),
                    1,
                )

    def test_real_http_world_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            world = WorldSceneService(Path(temp_dir) / "world.sqlite3", advance_minutes=0)
            config = BridgeConfig(token=TOKEN, port=0)
            app = BridgeApplication(config, scene_provider=world.open_scene, ack_handler=world.ack_scene)
            server = BridgeHTTPServer(config, app=app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/v1/scenes/open"
                opened = {
                    "protocol_version": 1,
                    "request_id": "http-world-1",
                    "client_session_id": "http-session",
                }
                request = Request(
                    url,
                    data=json.dumps(opened).encode(),
                    headers={"Content-Type": "application/json", "X-Open-Shift-Token": TOKEN},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode())
                self.assertEqual(payload["scene"]["return_to"], "bar")
                self.assertEqual(len(payload["scene"]["lines"]), 3)
                self.assertIn(
                    "jill",
                    {line["speaker_id"] for line in payload["scene"]["lines"]},
                )
                jill = next(
                    line
                    for line in payload["scene"]["lines"]
                    if line["speaker_id"] == "jill"
                )
                self.assertEqual(jill["portrait_id"], "")
                persisted = world.open_scene(opened)
                persisted_jill = next(
                    line for line in persisted.lines if line.speaker_id == "jill"
                )
                self.assertIsNone(persisted_jill.portrait_id)
                self.assertTrue(
                    all(
                        any("\u4e00" <= char <= "\u9fff" for char in line["text"])
                        for line in payload["scene"]["lines"]
                    )
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_daily_story_ambient_http_uses_legacy_empty_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            world = WorldSceneService(
                Path(temp_dir) / "world.sqlite3",
                provider_factory=MockProvider,
                advance_minutes=0,
                daily_story_mode=True,
            )
            config = BridgeConfig(token=TOKEN, port=0)
            app = BridgeApplication(
                config,
                scene_provider=world.open_scene,
                ack_handler=world.ack_scene,
                order_handler=world.resolve_order,
            )
            server = BridgeHTTPServer(config, app=app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opened = {
                "protocol_version": 1,
                "request_id": "http-ambient-1",
                "client_session_id": "http-ambient-session",
            }
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/v1/scenes/open",
                    data=json.dumps(opened).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "X-Open-Shift-Token": TOKEN,
                    },
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode())
                self.assertTrue(payload["scene"]["scene_id"].startswith("opening_"))
                self.assertTrue(
                    all(
                        line["speaker_id"] == "" and line["portrait_id"] == ""
                        for line in payload["scene"]["lines"]
                    )
                )
                replay = world.open_scene(opened)
                self.assertTrue(
                    all(
                        line.speaker_id is None and line.portrait_id is None
                        for line in replay.lines
                    )
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                world.wait_for_background_generation()

    def test_gamemaker_real_number_empty_drink_resolves_as_wrong(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "world.sqlite3"
            world = WorldSceneService(db_path, advance_minutes=0)
            app = BridgeApplication(
                BridgeConfig(token=TOKEN, port=0),
                scene_provider=world.open_scene,
                ack_handler=world.ack_scene,
                order_handler=world.resolve_order,
            )
            headers = {"X-Open-Shift-Token": TOKEN}
            opened = app.handle(
                "POST",
                "/v1/scenes/open",
                headers,
                json.dumps(
                    {
                        "protocol_version": 1.0,
                        "request_id": "gm-open-1",
                        "client_session_id": "gm-session-1",
                    }
                ).encode(),
            )
            self.assertEqual(opened.status, 200)
            scene = opened.body["scene"]
            order = scene["order"]
            acknowledged = app.handle(
                "POST",
                "/v1/scenes/ack",
                headers,
                json.dumps(
                    {
                        "protocol_version": 1.0,
                        "request_id": "gm-ack-1",
                        "client_session_id": "gm-session-1",
                        "scene_id": scene["scene_id"],
                        "outcome": "order_started",
                    }
                ).encode(),
            )
            self.assertEqual(acknowledged.status, 200)
            request = {
                "protocol_version": 1.0,
                "request_id": "gm-resolve-1",
                "client_session_id": "gm-session-1",
                "scene_id": scene["scene_id"],
                "order_id": order["order_id"],
                "drink": {
                    "adelhyde": 0.0,
                    "bronson_extract": 0.0,
                    "powdered_delta": 0.0,
                    "flanergide": 0.0,
                    "karmotrine": 0.0,
                    "ice": 0.0,
                    "aged": 0.0,
                    "preparation": "mixed",
                },
            }
            first = app.handle(
                "POST",
                "/v1/orders/resolve",
                headers,
                json.dumps(request).encode(),
            )
            replay = app.handle(
                "POST",
                "/v1/orders/resolve",
                headers,
                json.dumps(request).encode(),
            )
            self.assertEqual(first.status, 200)
            self.assertEqual(replay.body, first.body)
            self.assertEqual(first.body["result"]["category"], "wrong")
            with WorldStore(db_path) as store:
                served = [
                    event
                    for event in store.list_events()
                    if event["event_type"] == "drink_served"
                ]
                self.assertEqual(len(served), 1)
                self.assertEqual(served[0]["payload"]["drink"]["adelhyde"], 0)


class Stage4LauncherTests(unittest.TestCase):
    def test_runtime_file_is_atomic_and_cleanup_removes_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / "game" / "open-shift-runtime.ini"
            config = LaunchConfig(
                db_path=root / "world.sqlite3",
                runtime_file=runtime,
                game_command=("fake-game",),
                game_cwd=root,
            )
            session = RuntimeSession(config)
            session.write_runtime_file()
            contents = runtime.read_text(encoding="utf-8")
            self.assertIn("[bridge]", contents)
            self.assertIn(str(session.port), contents)
            self.assertIn(session.token, contents)
            self.assertIn(session.session_id, contents)
            self.assertGreaterEqual(len(session.session_id), 16)
            with self.assertRaisesRegex(Exception, "already exists"):
                session.write_runtime_file()
            session.cleanup()
            self.assertFalse(runtime.exists())

    def test_game_environment_does_not_receive_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = LaunchConfig(
                db_path=root / "world.sqlite3",
                runtime_file=root / "runtime.ini",
                game_command=("fake-game",),
                game_cwd=root,
                steam_app_id=447530,
            )
            session = RuntimeSession(config)
            with patch.dict(os.environ, {"OPEN_SHIFT_API_KEY": "secret-key"}, clear=False):
                with patch("subprocess.Popen") as popen:
                    fake = popen.return_value
                    fake.wait.return_value = 0
                    session.start_game()
                    env = popen.call_args.kwargs["env"]
            self.assertNotIn("OPEN_SHIFT_API_KEY", env)
            self.assertNotIn("OPEN_SHIFT_BRIDGE_TOKEN", env)
            self.assertEqual(env["SteamAppId"], "447530")
            self.assertEqual(env["SteamGameId"], "447530")

    def test_legacy_steam_root_changes_only_process_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = root / "game"
            steam = root / "Steam"
            game.mkdir()
            steam.mkdir()
            (game / "fake-game.exe").touch()
            (steam / "Steam2.dll").touch()
            session = RuntimeSession(
                LaunchConfig(
                    db_path=root / "world.sqlite3",
                    runtime_file=root / "runtime.ini",
                    game_command=("fake-game.exe",),
                    game_cwd=game,
                    steam_root=steam,
                )
            )
            with patch("subprocess.Popen") as popen:
                session.start_game()
            self.assertEqual(popen.call_args.args[0][0], str(game / "fake-game.exe"))
            self.assertEqual(popen.call_args.kwargs["cwd"], root)

    def test_legacy_steam_root_requires_steam2_dll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(Exception, "steam_root must contain Steam2.dll"):
                LaunchConfig(
                    db_path=root / "world.sqlite3",
                    runtime_file=root / "runtime.ini",
                    game_command=("fake-game",),
                    game_cwd=root,
                    steam_root=root / "Steam",
                )

    def test_invalid_steam_app_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(Exception, "steam_app_id must be positive"):
                LaunchConfig(
                    db_path=root / "world.sqlite3",
                    runtime_file=root / "runtime.ini",
                    game_command=("fake-game",),
                    game_cwd=root,
                    steam_app_id=0,
                )

    def test_bridge_readiness_fails_if_process_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = RuntimeSession(
                LaunchConfig(
                    db_path=root / "world.sqlite3",
                    runtime_file=root / "runtime.ini",
                    game_command=("fake-game",),
                    game_cwd=root,
                )
            )
            with patch("subprocess.Popen") as popen:
                popen.return_value.poll.return_value = 3
                session.start_bridge()
                with self.assertRaisesRegex(Exception, "code 3"):
                    session.wait_for_bridge(timeout_seconds=0.1)

    def test_real_launcher_process_round_trip_and_cleanup(self) -> None:
        fake_game = r'''
import configparser
import json
import pathlib
import sys
import time
import urllib.request

runtime = pathlib.Path(sys.argv[1])
result = pathlib.Path(sys.argv[2])
parser = configparser.ConfigParser()
parser.read(runtime, encoding="utf-8")
port = parser.getint("bridge", "port")
token = parser.get("bridge", "token")
base = f"http://127.0.0.1:{port}"
headers = {"Content-Type": "application/json", "X-Open-Shift-Token": token}
for index in range(20):
    opened = {"protocol_version": 1, "request_id": f"launcher-open-{index}", "client_session_id": "launcher-session"}
    request = urllib.request.Request(base + "/v1/scenes/open", data=json.dumps(opened).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        scene = json.loads(response.read().decode())["scene"]
    outcome = "order_started" if "order" in scene else "continued_in_bar"
    ack = {"protocol_version": 1, "request_id": f"launcher-ack-{index}", "client_session_id": "launcher-session", "scene_id": scene["scene_id"], "outcome": outcome}
    request = urllib.request.Request(base + "/v1/scenes/ack", data=json.dumps(ack).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        accepted = json.loads(response.read().decode())["status"]
    if "order" in scene:
        break
    time.sleep(0.02)
result.write_text(json.dumps({"scene_id": scene["scene_id"], "lines": len(scene["lines"]), "ack": accepted}), encoding="utf-8")
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / "open-shift-runtime.ini"
            result = root / "result.json"
            db_path = root / "world.sqlite3"
            config = LaunchConfig(
                db_path=db_path,
                runtime_file=runtime,
                game_command=(sys.executable, "-c", fake_game, str(runtime), str(result)),
                game_cwd=root,
                advance_minutes=60,
            )
            self.assertEqual(RuntimeSession(config).run(), 0)
            self.assertFalse(runtime.exists())
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(payload["scene_id"].startswith("day_"))
            self.assertEqual(payload["lines"], 3)
            self.assertEqual(payload["ack"], "accepted")
            with WorldStore(db_path) as store:
                self.assertEqual(store.current_tick, 0)
                self.assertGreaterEqual(
                    len([event for event in store.list_events() if event["event_type"] == "player_scene_ack"]),
                    3,
                )


if __name__ == "__main__":
    unittest.main()
