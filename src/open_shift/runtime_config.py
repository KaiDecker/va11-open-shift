"""Validated local runtime configuration with no secret-bearing fields."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .byok import APIProtocol, BYOKConfig, BYOKError, ResponseFormat, ThinkingMode


class RuntimeConfigError(ValueError):
    pass


_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_TOP_LEVEL = {"provider", "world"}
_PROVIDER_FIELDS = {
    "base_url",
    "model",
    "protocol",
    "response_format",
    "api_key_env",
    "timeout_seconds",
    "max_calls",
    "thinking",
}
_WORLD_FIELDS = {"prefetch_days"}


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    provider_base_url: str | None = None
    provider_model: str | None = None
    provider_protocol: APIProtocol = APIProtocol.CHAT_COMPLETIONS
    provider_response_format: ResponseFormat = ResponseFormat.JSON_OBJECT
    provider_api_key_env: str = "OPEN_SHIFT_API_KEY"
    provider_timeout_seconds: float = 30.0
    provider_max_calls: int = 100000
    provider_thinking: ThinkingMode = ThinkingMode.DEFAULT
    prefetch_days: int = 1

    def __post_init__(self) -> None:
        if (self.provider_base_url is None) != (self.provider_model is None):
            raise RuntimeConfigError("provider base_url and model must be configured together")
        if not _ENV_NAME.fullmatch(self.provider_api_key_env):
            raise RuntimeConfigError("provider api_key_env was not a safe environment name")
        if not 1.0 <= self.provider_timeout_seconds <= 120.0:
            raise RuntimeConfigError("provider timeout_seconds must be between 1 and 120")
        if not 1 <= self.provider_max_calls <= 100000:
            raise RuntimeConfigError("provider max_calls must be between 1 and 100000")
        if self.prefetch_days not in {0, 1}:
            raise RuntimeConfigError("world prefetch_days must be 0 or 1")
        if self.provider_base_url is not None:
            self.to_byok_config()

    def to_byok_config(self) -> BYOKConfig:
        if self.provider_base_url is None or self.provider_model is None:
            raise RuntimeConfigError("provider is not configured")
        try:
            return BYOKConfig(
                base_url=self.provider_base_url,
                model=self.provider_model,
                protocol=self.provider_protocol,
                response_format=self.provider_response_format,
                timeout_seconds=self.provider_timeout_seconds,
                api_key_env=self.provider_api_key_env,
                max_calls=self.provider_max_calls,
                thinking_mode=self.provider_thinking,
            )
        except (BYOKError, ValueError) as exc:
            raise RuntimeConfigError(str(exc)) from exc

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "provider": {
                "base_url": self.provider_base_url,
                "model": self.provider_model,
                "protocol": self.provider_protocol.value,
                "response_format": self.provider_response_format.value,
                "api_key_env": self.provider_api_key_env,
                "timeout_seconds": self.provider_timeout_seconds,
                "max_calls": self.provider_max_calls,
                "thinking": self.provider_thinking.value,
            },
            "world": {"prefetch_days": self.prefetch_days},
        }


def _reject_secret_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if "key" in str(key).lower() and key != "api_key_env":
                raise RuntimeConfigError(f"secret-bearing field was not allowed: {path}.{key}")
            _reject_secret_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        value = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeConfigError(f"runtime config could not be read: {exc}") from exc
    if set(value) - _TOP_LEVEL:
        raise RuntimeConfigError("runtime config contained unknown top-level fields")
    _reject_secret_keys(value)
    provider = value.get("provider", {})
    world = value.get("world", {})
    if not isinstance(provider, dict) or not isinstance(world, dict):
        raise RuntimeConfigError("provider and world sections must be tables")
    if set(provider) - _PROVIDER_FIELDS:
        raise RuntimeConfigError("provider config contained unknown fields")
    if set(world) - _WORLD_FIELDS:
        raise RuntimeConfigError("world config contained unknown fields")
    try:
        return RuntimeConfig(
            provider_base_url=provider.get("base_url"),
            provider_model=provider.get("model"),
            provider_protocol=APIProtocol(provider.get("protocol", APIProtocol.CHAT_COMPLETIONS.value)),
            provider_response_format=ResponseFormat(provider.get("response_format", ResponseFormat.JSON_OBJECT.value)),
            provider_api_key_env=provider.get("api_key_env", "OPEN_SHIFT_API_KEY"),
            provider_timeout_seconds=float(provider.get("timeout_seconds", 30.0)),
            provider_max_calls=int(provider.get("max_calls", 100000)),
            provider_thinking=ThinkingMode(provider.get("thinking", ThinkingMode.DEFAULT.value)),
            prefetch_days=int(world.get("prefetch_days", 1)),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError(f"runtime config values were invalid: {exc}") from exc
