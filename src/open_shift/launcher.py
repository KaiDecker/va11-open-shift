"""Secure local launcher for the bridge and a copied game executable."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .paired_saves import WorldSessionCheckpoint
from .diagnostics import emit_timing, monotonic_seconds


class LauncherError(RuntimeError):
    pass


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True, slots=True)
class LaunchConfig:
    db_path: Path
    runtime_file: Path
    game_command: tuple[str, ...]
    game_cwd: Path
    seed: int = 7
    port: int = 0
    advance_minutes: int = 1440
    bridge_command: tuple[str, ...] = ()
    bridge_extra_args: tuple[str, ...] = ()
    health_timeout_seconds: float = 10.0
    steam_root: Path | None = None
    steam_app_id: int | None = None
    prepare_story_before_game: bool = False
    story_prepare_timeout_seconds: float = 600.0

    def __post_init__(self) -> None:
        if not self.game_command:
            raise LauncherError("game_command cannot be empty")
        if self.port < 0 or self.port > 65535:
            raise LauncherError("port must be between 0 and 65535")
        if self.advance_minutes < 0 or self.advance_minutes > 43200:
            raise LauncherError("advance_minutes must be between 0 and 43200")
        if not 0.1 <= self.health_timeout_seconds <= 120.0:
            raise LauncherError("health_timeout_seconds must be between 0.1 and 120")
        if self.steam_root is not None and not (self.steam_root / "Steam2.dll").is_file():
            raise LauncherError("steam_root must contain Steam2.dll")
        if self.steam_app_id is not None and self.steam_app_id <= 0:
            raise LauncherError("steam_app_id must be positive")
        if not 1.0 <= self.story_prepare_timeout_seconds <= 1800.0:
            raise LauncherError(
                "story_prepare_timeout_seconds must be between 1 and 1800"
            )


@dataclass(slots=True)
class RuntimeSession:
    config: LaunchConfig
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    session_id: str = field(default_factory=lambda: secrets.token_hex(16))
    port: int = field(init=False)
    _bridge_process: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)
    _world_checkpoint: WorldSessionCheckpoint | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.port = self.config.port or _free_loopback_port()
        if len(self.token) < 16:
            raise LauncherError("runtime token generation failed")

    def write_runtime_file(self) -> Path:
        self.config.runtime_file.parent.mkdir(parents=True, exist_ok=True)
        if self.config.runtime_file.exists():
            raise LauncherError(
                "runtime file already exists; another session may be active"
            )
        parser = configparser.ConfigParser()
        parser["bridge"] = {
            "port": str(self.port),
            "token": self.token,
            "session_id": self.session_id,
        }
        fd, temp_name = tempfile.mkstemp(
            prefix="open-shift-", suffix=".ini", dir=self.config.runtime_file.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                parser.write(handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.config.runtime_file)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        try:
            self.config.runtime_file.chmod(0o600)
        except OSError:
            pass
        return self.config.runtime_file

    def _bridge_command(self) -> list[str]:
        if self.config.bridge_command:
            command = list(self.config.bridge_command)
        else:
            command = (
                [sys.executable, "serve-bridge"]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "open_shift", "serve-bridge"]
            )
        command.extend(
            [
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--token-env",
                "OPEN_SHIFT_BRIDGE_TOKEN",
                "--world-db",
                str(self.config.db_path),
                "--seed",
                str(self.config.seed),
                "--advance-minutes",
                str(self.config.advance_minutes),
            ]
        )
        command.extend(self.config.bridge_extra_args)
        return command

    def start_bridge(self) -> subprocess.Popen[bytes]:
        if self._bridge_process is not None:
            raise LauncherError("bridge was already started")
        env = os.environ.copy()
        env["OPEN_SHIFT_BRIDGE_TOKEN"] = self.token
        database_key = hashlib.sha256(
            str(self.config.db_path.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:16]
        checkpoint_path = self.config.runtime_file.with_name(
            f".open-shift-session-{database_key}.sqlite3"
        )
        self._world_checkpoint = WorldSessionCheckpoint(
            self.config.db_path, checkpoint_path
        )
        self._world_checkpoint.recover_abandoned_session()
        self._world_checkpoint.begin()
        env["OPEN_SHIFT_SESSION_CHECKPOINT"] = str(checkpoint_path)
        try:
            emit_timing("bridge_start", port=self.port)
            self._bridge_process = subprocess.Popen(
                self._bridge_command(),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                # Keep bridge startup diagnostics visible to the launcher user.
                # Runtime errors are intentionally short and never contain the
                # API key; hiding this stream turns a useful configuration error
                # into the opaque "exited with code 2" message.
                stderr=None,
            )
        except BaseException:
            checkpoint = self._world_checkpoint
            self._world_checkpoint = None
            if checkpoint is not None:
                checkpoint.cleanup()
            raise
        return self._bridge_process

    def wait_for_bridge(self, timeout_seconds: float = 10.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        url = f"http://127.0.0.1:{self.port}/v1/health"
        while time.monotonic() < deadline:
            if self._bridge_process is None:
                raise LauncherError("bridge was not started")
            return_code = self._bridge_process.poll()
            if return_code is not None:
                raise LauncherError(
                    f"bridge exited before becoming ready with code {return_code}"
                )
            try:
                with urllib.request.urlopen(url, timeout=0.25) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        raise LauncherError("bridge did not become ready before timeout")

    def start_game(self) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        env.pop("OPEN_SHIFT_API_KEY", None)
        env.pop("OPEN_SHIFT_BRIDGE_TOKEN", None)
        if self.config.steam_app_id is not None:
            app_id = str(self.config.steam_app_id)
            env["SteamAppId"] = app_id
            env["SteamGameId"] = app_id
        command = list(self.config.game_command)
        executable = Path(command[0])
        if not executable.is_absolute():
            candidate = self.config.game_cwd / executable
            if candidate.exists():
                command[0] = str(candidate.resolve())
        process_cwd = (
            self.config.steam_root.parent
            if self.config.steam_root is not None
            else self.config.game_cwd
        )
        return subprocess.Popen(
            command,
            cwd=process_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
        )

    def prepare_story(self) -> None:
        started = monotonic_seconds()
        emit_timing("story_prepare_start", timeout_seconds=self.config.story_prepare_timeout_seconds)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/story/prepare",
            data=json.dumps(
                {
                    "protocol_version": 1,
                    "request_id": f"launcher_prepare_{self.session_id}",
                    "client_session_id": self.session_id,
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Open-Shift-Token": self.token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.story_prepare_timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
                code = payload["error"]["code"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                code = f"http_{error.code}"
            raise LauncherError(f"story preparation failed: {code}") from None
        except (OSError, urllib.error.URLError) as error:
            raise LauncherError(
                f"story preparation request failed: {type(error).__name__}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LauncherError("story preparation returned invalid JSON") from None
        if (
            response.status != 200
            or payload.get("protocol_version") != 1
            or payload.get("request_id") != f"launcher_prepare_{self.session_id}"
            or payload.get("status") != "ready"
            or payload.get("shift_phase") != "playing"
            or isinstance(payload.get("last_completed_story_day"), bool)
            or not isinstance(payload.get("last_completed_story_day"), int)
            or payload["last_completed_story_day"] < 0
            or isinstance(payload.get("world_day"), bool)
            or not isinstance(payload.get("world_day"), int)
            or payload["world_day"] < 1
            or payload["last_completed_story_day"] >= payload["world_day"]
        ):
            raise LauncherError("story preparation returned an invalid response")
        emit_timing(
            "story_prepare_end",
            elapsed_ms=round((monotonic_seconds() - started) * 1000),
            world_day=payload.get("world_day"),
        )

    def stop_bridge(self) -> None:
        process = self._bridge_process
        self._bridge_process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def cleanup(self) -> None:
        try:
            self.config.runtime_file.unlink()
        except FileNotFoundError:
            pass

    def rollback_unsaved_world(self) -> None:
        checkpoint = self._world_checkpoint
        self._world_checkpoint = None
        if checkpoint is None:
            return
        try:
            checkpoint.rollback()
        finally:
            checkpoint.cleanup()

    def run(self) -> int:
        self.write_runtime_file()
        try:
            self.start_bridge()
            self.wait_for_bridge(self.config.health_timeout_seconds)
            if self.config.prepare_story_before_game:
                print(
                    "Preparing the local day skeleton before starting the game...",
                    flush=True,
                )
                self.prepare_story()
                print("The first Open Shift day is ready.", flush=True)
            game = self.start_game()
            return int(game.wait())
        finally:
            self.stop_bridge()
            self.rollback_unsaved_world()
            self.cleanup()


def build_launch_config(
    *,
    db_path: str | Path,
    runtime_file: str | Path,
    game_command: Sequence[str],
    game_cwd: str | Path,
    seed: int = 7,
    port: int = 0,
    advance_minutes: int = 1440,
    bridge_extra_args: Sequence[str] = (),
    bridge_command: Sequence[str] = (),
    health_timeout_seconds: float = 10.0,
    steam_root: str | Path | None = None,
    steam_app_id: int | None = None,
    prepare_story_before_game: bool = False,
    story_prepare_timeout_seconds: float = 600.0,
) -> LaunchConfig:
    return LaunchConfig(
        db_path=Path(db_path),
        runtime_file=Path(runtime_file),
        game_command=tuple(game_command),
        game_cwd=Path(game_cwd),
        seed=seed,
        port=port,
        advance_minutes=advance_minutes,
        bridge_extra_args=tuple(bridge_extra_args),
        bridge_command=tuple(bridge_command),
        health_timeout_seconds=health_timeout_seconds,
        steam_root=Path(steam_root) if steam_root is not None else None,
        steam_app_id=steam_app_id,
        prepare_story_before_game=prepare_story_before_game,
        story_prepare_timeout_seconds=story_prepare_timeout_seconds,
    )
