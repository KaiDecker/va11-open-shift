"""Secure local launcher for the bridge and a copied game executable."""

from __future__ import annotations

import configparser
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


@dataclass(slots=True)
class RuntimeSession:
    config: LaunchConfig
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    port: int = field(init=False)
    _bridge_process: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)

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
        parser["bridge"] = {"port": str(self.port), "token": self.token}
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
            command = [sys.executable, "-m", "open_shift", "serve-bridge"]
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
        self._bridge_process = subprocess.Popen(
            self._bridge_command(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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

    def run(self) -> int:
        self.write_runtime_file()
        try:
            self.start_bridge()
            self.wait_for_bridge(self.config.health_timeout_seconds)
            game = self.start_game()
            return int(game.wait())
        finally:
            self.stop_bridge()
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
    health_timeout_seconds: float = 10.0,
    steam_root: str | Path | None = None,
    steam_app_id: int | None = None,
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
        health_timeout_seconds=health_timeout_seconds,
        steam_root=Path(steam_root) if steam_root is not None else None,
        steam_app_id=steam_app_id,
    )
