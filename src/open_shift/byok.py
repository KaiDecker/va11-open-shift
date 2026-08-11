"""BYOK model provider with strict, provider-independent safety boundaries.

The HTTP request shapes are isolated here because live OpenAI documentation was
not reachable from the development environment. Contract tests use an injected
transport; users can select either the Responses or compatible Chat Completions
protocol when performing an explicit one-call probe.
"""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from .models import (
    ActionProposal,
    ActionType,
    DecisionContext,
    GoalStatus,
)


MAX_RESPONSE_BYTES = 1_000_000
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class APIProtocol(str, Enum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class BYOKError(RuntimeError):
    """Base exception safe to report without exposing credentials."""


class BYOKConfigurationError(BYOKError):
    pass


class BYOKTransportError(BYOKError):
    pass


class BYOKResponseError(BYOKError):
    pass


class BYOKValidationError(BYOKError):
    pass


class BYOKBudgetExceeded(BYOKError):
    pass


@dataclass(frozen=True, slots=True)
class BYOKConfig:
    base_url: str
    model: str
    protocol: APIProtocol = APIProtocol.RESPONSES
    timeout_seconds: float = 30.0
    api_key_env: str = "OPEN_SHIFT_API_KEY"
    max_calls: int = 1

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BYOKConfigurationError("base_url must be an absolute HTTP URL")
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not loopback:
            raise BYOKConfigurationError(
                "remote BYOK endpoints must use HTTPS; HTTP is loopback-only"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BYOKConfigurationError(
                "base_url cannot contain credentials, query parameters, or fragments"
            )
        if not self.model.strip() or len(self.model) > 200:
            raise BYOKConfigurationError("model must be a non-empty identifier")
        if not 1.0 <= self.timeout_seconds <= 120.0:
            raise BYOKConfigurationError("timeout_seconds must be between 1 and 120")
        if not _ENV_NAME.fullmatch(self.api_key_env):
            raise BYOKConfigurationError("api_key_env is not a safe environment name")
        if not 1 <= self.max_calls <= 100_000:
            raise BYOKConfigurationError("max_calls must be between 1 and 100000")

    @property
    def endpoint(self) -> str:
        suffix = (
            "responses"
            if self.protocol is APIProtocol.RESPONSES
            else "chat/completions"
        )
        return f"{self.base_url.rstrip('/')}/{suffix}"


class JsonTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(req.full_url, code, "redirect rejected", headers, fp)


class UrllibJsonTransport:
    """Small standard-library transport that never logs request headers."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_RejectRedirects())

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        data = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=dict(headers),
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise BYOKTransportError(f"provider returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise BYOKTransportError(
                f"provider request failed: {type(exc).__name__}"
            ) from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise BYOKResponseError("provider response exceeded size limit")
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BYOKResponseError("provider returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise BYOKResponseError("provider response must be a JSON object")
        return decoded


ACTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action_type": {
            "type": "string",
            "enum": [action.value for action in ActionType],
        },
        "target_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "location": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "duration_minutes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 720,
        },
        "reason_code": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]{0,63}$",
        },
    },
    "required": [
        "action_type",
        "target_id",
        "location",
        "duration_minutes",
        "reason_code",
    ],
}


_SYSTEM_INSTRUCTION = """You propose one action for one fictional character in a persistent simulation.
You never execute actions and never modify world state. Use only facts in the observation.
Return exactly the requested JSON object with no prose. Keep reason_code short and categorical.
Action constraints:
- travel requires a listed location and no target.
- message and talk require another listed agent as target and no location.
- visit_bar may target another listed agent or null; location must be null.
- work and rest require null target and null location.
The world rules will independently validate and may reject your proposal."""


def decision_observation(context: DecisionContext) -> dict[str, Any]:
    return {
        "world_tick": context.tick,
        "actor": {
            "agent_id": context.actor.agent_id,
            "display_name": context.actor.display_name,
            "location": context.actor.location,
            "money": context.actor.money,
            "fatigue": context.actor.fatigue,
            "mood": context.actor.mood,
        },
        "other_agents": [
            {
                "agent_id": agent.agent_id,
                "display_name": agent.display_name,
                "location": agent.location,
            }
            for agent in context.agents
            if agent.agent_id != context.actor.agent_id
        ],
        "relationships": [
            {
                "target_id": relationship.target_id,
                "trust": relationship.trust,
                "warmth": relationship.warmth,
                "debt": relationship.debt,
            }
            for relationship in context.relationships
        ],
        "active_goals": [
            {
                "goal_id": goal.goal_id,
                "kind": goal.kind,
                "target_id": goal.target_id,
                "target_value": goal.target_value,
                "priority": goal.priority,
            }
            for goal in context.goals
            if goal.status is GoalStatus.ACTIVE
        ],
        "allowed_locations": list(context.locations),
        "allowed_actions": [action.value for action in ActionType],
    }


def _responses_payload(config: BYOKConfig, context: DecisionContext) -> dict[str, Any]:
    return {
        "model": config.model,
        "instructions": _SYSTEM_INSTRUCTION,
        "input": json.dumps(
            decision_observation(context),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "action_proposal",
                "strict": True,
                "schema": ACTION_OUTPUT_SCHEMA,
            }
        },
    }


def _chat_payload(config: BYOKConfig, context: DecisionContext) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {
                "role": "user",
                "content": json.dumps(
                    decision_observation(context),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "action_proposal",
                "strict": True,
                "schema": ACTION_OUTPUT_SCHEMA,
            },
        },
    }


def _extract_responses_output(response: Mapping[str, Any]) -> str | dict[str, Any]:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("json"), dict):
                    return part["json"]
                text = part.get("text")
                if isinstance(text, str):
                    return text
    raise BYOKResponseError("Responses payload contained no usable output text")


def _extract_chat_output(response: Mapping[str, Any]) -> str | dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise BYOKResponseError("chat payload contained no choices")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise BYOKResponseError("chat choice contained no message")
    content = choice["message"].get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        return content
    raise BYOKResponseError("chat message contained no usable content")


def _as_action_object(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise BYOKResponseError("model output was not a JSON object") from None
    if not isinstance(value, dict):
        raise BYOKResponseError("model output must be a JSON object")
    return value


def validate_action_output(
    value: Mapping[str, Any], context: DecisionContext
) -> ActionProposal:
    required = {
        "action_type",
        "target_id",
        "location",
        "duration_minutes",
        "reason_code",
    }
    if set(value) != required:
        raise BYOKValidationError("action output fields did not match the schema")

    action_raw = value["action_type"]
    if not isinstance(action_raw, str):
        raise BYOKValidationError("action_type must be a string")
    try:
        action_type = ActionType(action_raw)
    except ValueError:
        raise BYOKValidationError("action_type was not allowed") from None

    target_id = value["target_id"]
    location = value["location"]
    duration = value["duration_minutes"]
    reason_code = value["reason_code"]
    if target_id is not None and not isinstance(target_id, str):
        raise BYOKValidationError("target_id must be a string or null")
    if location is not None and not isinstance(location, str):
        raise BYOKValidationError("location must be a string or null")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= 720:
        raise BYOKValidationError("duration_minutes must be an integer from 0 to 720")
    if not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code):
        raise BYOKValidationError("reason_code was invalid")

    valid_targets = {
        agent.agent_id
        for agent in context.agents
        if agent.agent_id != context.actor.agent_id
    }
    if target_id is not None and target_id not in valid_targets:
        raise BYOKValidationError("target_id was not visible to the actor")
    if location is not None and location not in context.locations:
        raise BYOKValidationError("location was not allowed")

    if action_type is ActionType.TRAVEL:
        if location is None or target_id is not None:
            raise BYOKValidationError("travel requires location and null target")
    elif action_type in {ActionType.MESSAGE, ActionType.TALK}:
        if target_id is None or location is not None:
            raise BYOKValidationError(
                "message and talk require target and null location"
            )
    elif action_type is ActionType.VISIT_BAR:
        if location is not None:
            raise BYOKValidationError("visit_bar requires null location")
    elif target_id is not None or location is not None:
        raise BYOKValidationError("work and rest require null target and location")

    return ActionProposal(
        action_type=action_type,
        target_id=target_id,
        location=location,
        duration_minutes=duration,
        reason_code=reason_code,
    )


@dataclass(slots=True)
class BYOKProvider:
    config: BYOKConfig
    _api_key: str = field(repr=False)
    transport: JsonTransport = field(default_factory=UrllibJsonTransport, repr=False)
    calls_used: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self._api_key or not self._api_key.strip():
            raise BYOKConfigurationError("API key was empty")

    @classmethod
    def from_env(
        cls,
        config: BYOKConfig,
        *,
        transport: JsonTransport | None = None,
    ) -> "BYOKProvider":
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise BYOKConfigurationError(
                f"API key environment variable {config.api_key_env} was not set"
            )
        return cls(
            config=config,
            _api_key=api_key,
            transport=transport or UrllibJsonTransport(),
        )

    def decide(self, context: DecisionContext) -> ActionProposal:
        if self.calls_used >= self.config.max_calls:
            raise BYOKBudgetExceeded("provider call budget was exhausted")
        self.calls_used += 1
        payload = (
            _responses_payload(self.config, context)
            if self.config.protocol is APIProtocol.RESPONSES
            else _chat_payload(self.config, context)
        )
        response = self.transport.post_json(
            url=self.config.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            payload=payload,
            timeout_seconds=self.config.timeout_seconds,
        )
        raw = (
            _extract_responses_output(response)
            if self.config.protocol is APIProtocol.RESPONSES
            else _extract_chat_output(response)
        )
        return validate_action_output(_as_action_object(raw), context)
