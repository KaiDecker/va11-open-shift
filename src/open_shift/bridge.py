"""Loopback-only HTTP bridge between GameMaker and the world service."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .drinks import DrinkOrder, ServiceResult
from .diagnostics import emit_timing, monotonic_seconds


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_TEXT_CHARACTERS = 240
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

AGENT_SPEAKERS = frozenset({"dana", "dorothy", "alma", "stella", "sei"})
ALLOWED_SPEAKERS = AGENT_SPEAKERS | {"jill"}
SPEAKER_PORTRAITS = {
    "dana": "sprite_dana",
    "dorothy": "sprite_doro",
    "alma": "sprite_alma",
    "stella": "sprite_stella",
    "sei": "sprite_sei",
    "jill": None,
}
ALLOWED_PORTRAITS = frozenset(
    portrait for portrait in SPEAKER_PORTRAITS.values() if portrait is not None
)
ALLOWED_EXPRESSIONS = frozenset({"neutral", "happy", "worry", "playful"})
ALLOWED_RETURN_TARGETS = frozenset({"bar"})


class BridgeError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SceneLine:
    line_id: str
    speaker_id: str | None
    portrait_id: str | None
    expression_id: str
    text: str

    def __post_init__(self) -> None:
        if not _RESOURCE_ID.fullmatch(self.line_id):
            raise ValueError("line_id was invalid")
        if self.speaker_id is None:
            if self.portrait_id is not None or self.expression_id != "neutral":
                raise ValueError("environment line must not have a portrait or expression")
        elif self.speaker_id not in ALLOWED_SPEAKERS:
            raise ValueError("speaker_id was not allowed")
        elif self.speaker_id == "jill":
            if self.portrait_id is not None:
                raise ValueError("Jill must not have a portrait_id")
        elif self.portrait_id not in ALLOWED_PORTRAITS:
            raise ValueError("portrait_id was not allowed")
        if self.speaker_id is not None and self.portrait_id != SPEAKER_PORTRAITS[
            self.speaker_id
        ]:
            raise ValueError("portrait_id did not match speaker_id")
        if self.expression_id not in ALLOWED_EXPRESSIONS:
            raise ValueError("expression_id was not allowed")
        if not self.text or len(self.text) > MAX_TEXT_CHARACTERS:
            raise ValueError("scene text length was invalid")
        if any(ord(character) < 32 for character in self.text):
            raise ValueError("scene text contained a control character")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "line_id": self.line_id,
            "speaker_id": self.speaker_id,
            "portrait_id": self.portrait_id,
            "expression_id": self.expression_id,
            "text": self.text,
        }

    def to_gamemaker_dict(self) -> dict[str, str]:
        """Serialize the line for the legacy GameMaker JSON decoder."""

        return {
            "line_id": self.line_id,
            "speaker_id": self.speaker_id or "",
            "portrait_id": self.portrait_id or "",
            "expression_id": self.expression_id,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ScenePackage:
    scene_id: str
    lines: tuple[SceneLine, ...]
    return_to: str = "bar"
    order: DrinkOrder | None = None

    def __post_init__(self) -> None:
        if not _RESOURCE_ID.fullmatch(self.scene_id):
            raise ValueError("scene_id was invalid")
        if not 1 <= len(self.lines) <= 8:
            raise ValueError("scene must contain between 1 and 8 lines")
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("scene line identifiers must be unique")
        if self.return_to not in ALLOWED_RETURN_TARGETS:
            raise ValueError("return target was not allowed")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "scene_id": self.scene_id,
            "lines": [line.to_dict() for line in self.lines],
            "return_to": self.return_to,
        }
        if self.order is not None:
            value["order"] = self.order.to_dict()
        return value

    def to_gamemaker_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value["lines"] = [line.to_gamemaker_dict() for line in self.lines]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScenePackage":
        if set(value) not in (
            {"scene_id", "lines", "return_to"},
            {"scene_id", "lines", "return_to", "order"},
        ):
            raise ValueError("persisted scene fields did not match the schema")
        raw_lines = value["lines"]
        if not isinstance(raw_lines, list):
            raise ValueError("persisted scene lines must be a list")
        lines: list[SceneLine] = []
        for item in raw_lines:
            if not isinstance(item, dict) or set(item) != {
                "line_id",
                "speaker_id",
                "portrait_id",
                "expression_id",
                "text",
            }:
                raise ValueError("persisted scene line fields did not match the schema")
            if not all(
                isinstance(field, str)
                or (key in {"speaker_id", "portrait_id"} and field is None)
                for key, field in item.items()
            ):
                raise ValueError("persisted scene line values must be strings")
            lines.append(
                SceneLine(
                    item["line_id"],
                    item["speaker_id"] or None,
                    item["portrait_id"],
                    item["expression_id"],
                    item["text"],
                )
            )
        scene_id = value["scene_id"]
        return_to = value["return_to"]
        if not isinstance(scene_id, str) or not isinstance(return_to, str):
            raise ValueError("persisted scene identifiers must be strings")
        raw_order = value.get("order")
        if raw_order is not None and not isinstance(raw_order, dict):
            raise ValueError("persisted scene order must be an object")
        order = DrinkOrder.from_dict(raw_order) if raw_order is not None else None
        return cls(scene_id, tuple(lines), return_to, order)


@dataclass(frozen=True, slots=True)
class OrderResolution:
    result: ServiceResult
    scene: ScenePackage
    income_delta: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.income_delta, bool) or not isinstance(
            self.income_delta, int
        ) or self.income_delta < 0:
            raise ValueError("order income_delta was invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "scene": self.scene.to_dict(),
            "income_delta": self.income_delta,
        }

    def to_gamemaker_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "scene": self.scene.to_gamemaker_dict(),
            "income_delta": self.income_delta,
        }


def stage_three_scene() -> ScenePackage:
    """Fixed three-line scene used by the Stage 3 GameMaker connection."""

    return ScenePackage(
        scene_id="stage_3_connection_test",
        lines=(
            SceneLine(
                "connection_1",
                "dana",
                "sprite_dana",
                "happy",
                "Open Shift 本地服务已连接。",
            ),
            SceneLine(
                "connection_2",
                "alma",
                "sprite_alma",
                "neutral",
                "场景文本正在作为普通字符安全显示。",
            ),
            SceneLine(
                "connection_3",
                "dana",
                "sprite_dana",
                "neutral",
                "测试结束后会留在酒吧继续运行。",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    token: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 8711

    def __post_init__(self) -> None:
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            raise ValueError("bridge host must be a loopback IP address") from None
        if not address.is_loopback:
            raise ValueError("bridge may only listen on a loopback address")
        if not 0 <= self.port <= 65535:
            raise ValueError("bridge port must be between 0 and 65535")
        if not 16 <= len(self.token) <= 256 or any(
            ord(character) < 33 or ord(character) > 126 for character in self.token
        ):
            raise ValueError("bridge token must be 16-256 printable ASCII characters")


@dataclass(frozen=True, slots=True)
class BridgeResponse:
    status: int
    body: dict[str, Any]


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _require_object(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_REQUEST_BYTES:
        raise BridgeError(413, "request_too_large", "request body exceeded size limit")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BridgeError(400, "invalid_json", "request body was not valid JSON") from None
    if not isinstance(value, dict):
        raise BridgeError(400, "invalid_request", "request body must be a JSON object")
    return value


def _require_fields(
    value: Mapping[str, Any], required: set[str]
) -> None:
    if set(value) != required:
        raise BridgeError(
            400, "invalid_request", "request fields did not match the protocol"
        )


def _require_request_id(value: Any) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise BridgeError(400, "invalid_request_id", "request_id was invalid")
    return value


class BridgeApplication:
    """Pure request handler; no model calls and no authoritative world writes."""

    def __init__(
        self,
        config: BridgeConfig,
        scene: ScenePackage | None = None,
        *,
        scene_provider: Callable[[Mapping[str, Any]], ScenePackage] | None = None,
        scene_hint_provider: Callable[[Mapping[str, Any]], str | None] | None = None,
        ack_handler: Callable[[Mapping[str, Any]], None] | None = None,
        order_handler: Callable[[Mapping[str, Any]], OrderResolution] | None = None,
        save_pair_handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        save_restore_handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        tablet_feed_handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        story_prepare_handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        error_reporter: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self.config = config
        self.scene = scene or stage_three_scene()
        self._scene_provider = scene_provider
        self._scene_hint_provider = scene_hint_provider
        self._ack_handler = ack_handler
        self._order_handler = order_handler
        self._save_pair_handler = save_pair_handler
        self._save_restore_handler = save_restore_handler
        self._tablet_feed_handler = tablet_feed_handler
        self._story_prepare_handler = story_prepare_handler
        self._error_reporter = error_reporter
        self._open_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._ack_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._order_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._save_pair_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._save_restore_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._tablet_feed_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._story_prepare_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        # Scene jobs keep the HTTP request short while the provider works in a
        # background thread.  The legacy synchronous endpoint remains intact
        # for older clients and as a fallback.
        self._scene_jobs: dict[str, dict[str, Any]] = {}
        self._scene_jobs_by_request: dict[str, str] = {}
        self._cache_lock = threading.Lock()

    def _authenticate(self, headers: Mapping[str, str]) -> None:
        origin = headers.get("Origin") or headers.get("origin")
        if origin:
            raise BridgeError(403, "browser_origin_rejected", "browser requests are not allowed")
        supplied = headers.get("X-Open-Shift-Token") or headers.get(
            "x-open-shift-token", ""
        )
        if not hmac.compare_digest(supplied, self.config.token):
            raise BridgeError(401, "unauthorized", "bridge token was missing or invalid")

    @staticmethod
    def _idempotent(
        cache: dict[str, tuple[str, dict[str, Any]]],
        request_id: str,
        request: Mapping[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        digest = hashlib.sha256(_canonical_json(request)).hexdigest()
        prior = cache.get(request_id)
        if prior is not None:
            if not hmac.compare_digest(prior[0], digest):
                raise BridgeError(
                    409,
                    "request_id_conflict",
                    "request_id was already used with different content",
                )
            return prior[1]
        cache[request_id] = (digest, response)
        return response

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes = b"",
    ) -> BridgeResponse:
        route = urlsplit(path).path
        try:
            if method == "GET" and route == "/v1/health":
                return BridgeResponse(
                    200,
                    {
                        "service": "open-shift-bridge",
                        "protocol_version": PROTOCOL_VERSION,
                        "status": "ready",
                    },
                )
            self._authenticate(headers)
            if method == "GET" and route.startswith("/v1/scenes/jobs/"):
                if route.endswith("/result"):
                    job_id = route.split("/")[-2]
                    result = self._scene_job_result(job_id)
                    return BridgeResponse(result.pop("_http_status", 200), result)
                job_id = route.rsplit("/", 1)[-1]
                return BridgeResponse(200, self._scene_job_status(job_id))
            if method == "POST" and route == "/v1/scenes/jobs":
                try:
                    return BridgeResponse(202, self._submit_scene_job(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("scene job submission", error)
                    raise BridgeError(
                        503,
                        "scene_job_unavailable",
                        "the world service could not start a scene job",
                    ) from None
            if method == "POST" and route == "/v1/scenes/open":
                try:
                    return BridgeResponse(200, self._open_scene(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("scene generation", error)
                    raise BridgeError(
                        503,
                        "scene_provider_unavailable",
                        "the world service could not produce a scene",
                    ) from None
            if method == "POST" and route == "/v1/scenes/ack":
                try:
                    return BridgeResponse(200, self._ack_scene(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("scene acknowledgement", error)
                    raise BridgeError(
                        503,
                        "scene_ack_unavailable",
                        "the world service could not record the scene result",
                    ) from None
            if method == "POST" and route == "/v1/orders/resolve":
                try:
                    return BridgeResponse(200, self._resolve_order(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("drink resolution", error)
                    raise BridgeError(
                        503,
                        "order_resolution_unavailable",
                        "the world service could not resolve the served drink",
                    ) from None
            if method == "POST" and route == "/v1/saves/pair":
                try:
                    return BridgeResponse(200, self._pair_save(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("paired save snapshot", error)
                    raise BridgeError(
                        503,
                        "paired_save_unavailable",
                        "the world service could not create the paired save",
                    ) from None
            if method == "POST" and route == "/v1/saves/restore":
                try:
                    return BridgeResponse(200, self._restore_save(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("paired save restore", error)
                    raise BridgeError(
                        503,
                        "paired_restore_unavailable",
                        "the world service could not restore the paired save",
                    ) from None
            if method == "POST" and route == "/v1/tablet/feed":
                try:
                    return BridgeResponse(200, self._tablet_feed(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("tablet feed", error)
                    raise BridgeError(
                        503,
                        "tablet_feed_unavailable",
                        "the world service could not produce the tablet feed",
                    ) from None
            if method == "POST" and route == "/v1/story/prepare":
                try:
                    return BridgeResponse(200, self._prepare_story(_require_object(body)))
                except BridgeError:
                    raise
                except Exception as error:
                    self._report_error("daily story preparation", error)
                    error_codes = {
                        "BYOKBudgetExceeded": (429, "provider_budget_exhausted"),
                        "BYOKConfigurationError": (503, "provider_configuration_error"),
                        "BYOKTransportError": (503, "provider_transport_error"),
                        "BYOKResponseError": (503, "provider_response_error"),
                        "BYOKValidationError": (503, "provider_validation_error"),
                    }
                    if type(error).__name__ in error_codes:
                        status, code = error_codes[type(error).__name__]
                        raise BridgeError(
                            status, code, "the configured provider was unavailable"
                        ) from None
                    raise BridgeError(
                        503,
                        "story_preparation_unavailable",
                        "the world service could not prepare the story day",
                    ) from None
            raise BridgeError(404, "not_found", "route was not found")
        except BridgeError as error:
            return BridgeResponse(
                error.status,
                {"error": {"code": error.code, "message": error.message}},
            )

    def _report_error(self, operation: str, error: Exception) -> None:
        if self._error_reporter is None:
            return
        try:
            self._error_reporter(operation, error)
        except Exception:
            pass

    def _open_scene(self, request: dict[str, Any]) -> dict[str, Any]:
        _require_fields(
            request, {"protocol_version", "request_id", "client_session_id"}
        )
        request_id = _require_request_id(request["request_id"])
        _require_request_id(request["client_session_id"])
        if request["protocol_version"] != PROTOCOL_VERSION:
            raise BridgeError(
                409, "protocol_mismatch", "client protocol version was not supported"
            )
        with self._cache_lock:
            prior = self._open_requests.get(request_id)
            if prior is not None:
                return self._idempotent(
                    self._open_requests, request_id, request, prior[1]
                )
            scene = (
                self._scene_provider(request)
                if self._scene_provider is not None
                else self.scene
            )
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "scene": scene.to_gamemaker_dict(),
            }
            return self._idempotent(
                self._open_requests, request_id, request, response
            )

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _submit_scene_job(self, request: dict[str, Any]) -> dict[str, Any]:
        """Queue scene generation and return a pollable, secret-free job record."""

        _require_fields(request, {"protocol_version", "request_id", "client_session_id"})
        request_id = _require_request_id(request["request_id"])
        _require_request_id(request["client_session_id"])
        if request["protocol_version"] != PROTOCOL_VERSION:
            raise BridgeError(409, "protocol_mismatch", "client protocol version was not supported")
        request_digest = hashlib.sha256(_canonical_json(request)).hexdigest()
        with self._cache_lock:
            existing_id = self._scene_jobs_by_request.get(request_id)
            if existing_id is not None:
                existing = self._scene_jobs[existing_id]
                if not hmac.compare_digest(existing["request_digest"], request_digest):
                    raise BridgeError(409, "request_id_conflict", "request_id was already used with different content")
                return self._public_scene_job(existing)
            job_id = "job_" + request_id
            now = self._utc_timestamp()
            record: dict[str, Any] = {
                "job_id": job_id,
                "request_id": request_id,
                "request": dict(request),
                "request_digest": request_digest,
                "status": "queued",
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "elapsed_ms": None,
                "attempt": 1,
                "scene": None,
                "error": None,
                "speaker_hint": None,
            }
            if self._scene_hint_provider is not None:
                hint = self._scene_hint_provider(request)
                if hint is not None and hint not in ALLOWED_SPEAKERS:
                    raise ValueError("scene speaker hint was invalid")
                record["speaker_hint"] = hint
            self._scene_jobs[job_id] = record
            self._scene_jobs_by_request[request_id] = job_id
        emit_timing("scene_job_queued", request_id=request_id, job_id=job_id, attempt=1)
        worker = threading.Thread(
            target=self._run_scene_job,
            args=(job_id,),
            name=f"open-shift-scene-{request_id}",
            # A non-daemon worker lets the interpreter flush the final timing
            # record before exit. Provider calls have their own bounded
            # timeout, so this cannot leave the player stuck indefinitely.
            daemon=False,
        )
        worker.start()
        return self._public_scene_job(record)

    def _public_scene_job(self, record: Mapping[str, Any]) -> dict[str, Any]:
        value = {
            "protocol_version": PROTOCOL_VERSION,
            "job_id": record["job_id"],
            "request_id": record["request_id"],
            "status": record["status"],
            "created_at": record["created_at"],
            "started_at": record["started_at"],
            "completed_at": record["completed_at"],
            "elapsed_ms": record["elapsed_ms"],
            "attempt": record["attempt"],
            "speaker_hint": record.get("speaker_hint") or "",
        }
        if record.get("scene") is not None:
            value["scene"] = record["scene"]
        if record.get("error") is not None:
            value["error"] = record["error"]
        return value

    def _run_scene_job(self, job_id: str) -> None:
        with self._cache_lock:
            record = self._scene_jobs.get(job_id)
            if record is None:
                return
            record["status"] = "running"
            record["started_at"] = self._utc_timestamp()
            request = dict(record["request"])
        started = monotonic_seconds()
        emit_timing("scene_job_started", request_id=record["request_id"], job_id=job_id, attempt=1)
        try:
            scene = self._scene_provider(request) if self._scene_provider is not None else self.scene
            if not isinstance(scene, ScenePackage):
                raise TypeError("scene provider returned an invalid package")
        except Exception as error:
            elapsed_ms = round((monotonic_seconds() - started) * 1000)
            error_code = type(error).__name__
            with self._cache_lock:
                record["status"] = "failed"
                record["completed_at"] = self._utc_timestamp()
                record["elapsed_ms"] = elapsed_ms
                record["error"] = {"code": error_code, "message": "scene generation failed"}
            emit_timing("scene_job_failed", request_id=record["request_id"], job_id=job_id, elapsed_ms=elapsed_ms, error_type=error_code)
            self._report_error("scene job generation", error)
            return
        elapsed_ms = round((monotonic_seconds() - started) * 1000)
        with self._cache_lock:
            record["status"] = "ready"
            record["completed_at"] = self._utc_timestamp()
            record["elapsed_ms"] = elapsed_ms
            record["scene"] = scene.to_gamemaker_dict()
        emit_timing("scene_job_ready", request_id=record["request_id"], job_id=job_id, elapsed_ms=elapsed_ms, line_count=len(scene.lines))

    def _scene_job_status(self, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"job_[A-Za-z0-9][A-Za-z0-9._-]{0,63}", job_id):
            raise BridgeError(400, "invalid_job_id", "scene job identifier was invalid")
        with self._cache_lock:
            record = self._scene_jobs.get(job_id)
            if record is None:
                raise BridgeError(404, "unknown_job", "scene job was not found")
            return self._public_scene_job(record)

    def _scene_job_result(self, job_id: str) -> dict[str, Any]:
        """Return a legacy scene response once a job is ready.

        This bridge keeps the GameMaker decoder small: pending results are
        HTTP 202, while a ready result deliberately has the same three-field
        shape as ``/v1/scenes/open``.
        """

        if not re.fullmatch(r"job_[A-Za-z0-9][A-Za-z0-9._-]{0,63}", job_id):
            raise BridgeError(400, "invalid_job_id", "scene job identifier was invalid")
        with self._cache_lock:
            record = self._scene_jobs.get(job_id)
            if record is None:
                raise BridgeError(404, "unknown_job", "scene job was not found")
            status = record["status"]
            if status != "ready":
                return {
                    "_http_status": 202,
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": record["request_id"],
                    "job_id": job_id,
                    "status": status,
                }
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": record["request_id"],
                "scene": record["scene"],
            }

    def _ack_scene(self, request: dict[str, Any]) -> dict[str, Any]:
        _require_fields(
            request,
            {
                "protocol_version",
                "request_id",
                "client_session_id",
                "scene_id",
                "outcome",
            },
        )
        request_id = _require_request_id(request["request_id"])
        _require_request_id(request["client_session_id"])
        if request["protocol_version"] != PROTOCOL_VERSION:
            raise BridgeError(
                409, "protocol_mismatch", "client protocol version was not supported"
            )
        if self._ack_handler is None and request["scene_id"] != self.scene.scene_id:
            raise BridgeError(404, "unknown_scene", "scene_id was not known")
        if request["outcome"] not in {"continued_in_bar", "order_started"}:
            raise BridgeError(400, "invalid_outcome", "scene outcome was not allowed")
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "scene_id": request["scene_id"],
            "status": "accepted",
        }
        with self._cache_lock:
            prior = self._ack_requests.get(request_id)
            if prior is None and self._ack_handler is not None:
                try:
                    self._ack_handler(request)
                except BridgeError:
                    raise
                except (KeyError, ValueError) as exc:
                    raise BridgeError(404, "unknown_scene", str(exc)) from None
            return self._idempotent(
                self._ack_requests, request_id, request, response
            )

    def _resolve_order(self, request: dict[str, Any]) -> dict[str, Any]:
        _require_fields(
            request,
            {
                "protocol_version",
                "request_id",
                "client_session_id",
                "scene_id",
                "order_id",
                "drink",
            },
        )
        request_id = _require_request_id(request["request_id"])
        _require_request_id(request["client_session_id"])
        _require_request_id(request["scene_id"])
        _require_request_id(request["order_id"])
        if request["protocol_version"] != PROTOCOL_VERSION:
            raise BridgeError(
                409, "protocol_mismatch", "client protocol version was not supported"
            )
        if not isinstance(request["drink"], dict):
            raise BridgeError(400, "invalid_drink", "drink must be a JSON object")
        if self._order_handler is None:
            raise BridgeError(404, "unknown_order", "order resolution was not enabled")
        with self._cache_lock:
            prior = self._order_requests.get(request_id)
            if prior is not None:
                return self._idempotent(
                    self._order_requests, request_id, request, prior[1]
                )
            try:
                resolution = self._order_handler(request)
            except BridgeError:
                raise
            except (KeyError, ValueError) as exc:
                raise BridgeError(400, "invalid_drink", str(exc)) from None
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                **resolution.to_gamemaker_dict(),
            }
            return self._idempotent(
                self._order_requests, request_id, request, response
            )

    def _save_operation(
        self,
        request: dict[str, Any],
        *,
        handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
        cache: dict[str, tuple[str, dict[str, Any]]],
        unavailable_code: str,
        expected_status: str,
    ) -> dict[str, Any]:
        _require_fields(
            request,
            {"protocol_version", "request_id", "client_session_id", "slot"},
        )
        request_id = _require_request_id(request["request_id"])
        _require_request_id(request["client_session_id"])
        raw_slot = request["slot"]
        if request["protocol_version"] != PROTOCOL_VERSION:
            raise BridgeError(
                409, "protocol_mismatch", "client protocol version was not supported"
            )
        if (
            isinstance(raw_slot, bool)
            or not isinstance(raw_slot, (int, float))
            or not float(raw_slot).is_integer()
            or not 1 <= raw_slot <= 24
        ):
            raise BridgeError(400, "invalid_save_slot", "save slot must be between 1 and 24")
        slot = int(raw_slot)
        if handler is None:
            raise BridgeError(404, unavailable_code, "paired saves were not enabled")
        with self._cache_lock:
            prior = cache.get(request_id)
            if prior is not None:
                return self._idempotent(cache, request_id, request, prior[1])
            handler_request = dict(request)
            handler_request["slot"] = slot
            result = dict(handler(handler_request))
            if set(result) != {"slot", "revision", "status", "world_day"}:
                raise BridgeError(
                    503, "invalid_save_response", "paired save response was invalid"
                )
            if (
                result["slot"] != slot
                or result["status"] != expected_status
                or not isinstance(result["revision"], str)
                or len(result["revision"]) != 32
                or isinstance(result["world_day"], bool)
                or not isinstance(result["world_day"], int)
                or result["world_day"] < 1
            ):
                raise BridgeError(
                    503, "invalid_save_response", "paired save response was invalid"
                )
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                **result,
            }
            return self._idempotent(cache, request_id, request, response)

    def _pair_save(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._save_operation(
            request,
            handler=self._save_pair_handler,
            cache=self._save_pair_requests,
            unavailable_code="paired_save_disabled",
            expected_status="paired",
        )

    def _restore_save(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._save_operation(
            request,
            handler=self._save_restore_handler,
            cache=self._save_restore_requests,
            unavailable_code="paired_restore_disabled",
            expected_status="restored",
        )

    def _tablet_feed(self, request: dict[str, Any]) -> dict[str, Any]:
        _require_fields(
            request,
            {"protocol_version", "request_id", "client_session_id", "limit"},
        )
        request_id = _require_request_id(request["request_id"])
        _require_request_id(request["client_session_id"])
        if request["protocol_version"] != PROTOCOL_VERSION:
            raise BridgeError(
                409, "protocol_mismatch", "client protocol version was not supported"
            )
        raw_limit = request["limit"]
        if (
            isinstance(raw_limit, bool)
            or not isinstance(raw_limit, (int, float))
            or not float(raw_limit).is_integer()
            or not 1 <= raw_limit <= 8
        ):
            raise BridgeError(400, "invalid_feed_limit", "tablet feed limit was invalid")
        if self._tablet_feed_handler is None:
            raise BridgeError(404, "tablet_feed_disabled", "tablet feed was not enabled")
        with self._cache_lock:
            prior = self._tablet_feed_requests.get(request_id)
            if prior is not None:
                return self._idempotent(
                    self._tablet_feed_requests, request_id, request, prior[1]
                )
            handler_request = dict(request)
            handler_request["limit"] = int(raw_limit)
            result = dict(self._tablet_feed_handler(handler_request))
            if set(result) != {"world_day", "items"}:
                raise BridgeError(503, "invalid_feed_response", "tablet feed response was invalid")
            if (
                isinstance(result["world_day"], bool)
                or not isinstance(result["world_day"], int)
                or result["world_day"] < 1
                or not isinstance(result["items"], list)
                or len(result["items"]) > int(raw_limit)
            ):
                raise BridgeError(503, "invalid_feed_response", "tablet feed response was invalid")
            item_fields = {
                "event_id",
                "event_key",
                "category",
                "status",
                "headline",
                "summary",
                "occurred_tick",
                "affected_agents",
            }
            for item in result["items"]:
                if not isinstance(item, dict) or set(item) != item_fields:
                    raise BridgeError(503, "invalid_feed_response", "tablet feed response was invalid")
                if (
                    isinstance(item["event_id"], bool)
                    or not isinstance(item["event_id"], int)
                    or item["event_id"] < 1
                    or isinstance(item["occurred_tick"], bool)
                    or not isinstance(item["occurred_tick"], int)
                    or item["occurred_tick"] < 0
                    or not all(isinstance(item[key], str) for key in ("event_key", "category", "status", "headline", "summary"))
                    or not isinstance(item["affected_agents"], list)
                    or not all(isinstance(agent, str) for agent in item["affected_agents"])
                ):
                    raise BridgeError(503, "invalid_feed_response", "tablet feed response was invalid")
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                **result,
            }
            return self._idempotent(
                self._tablet_feed_requests, request_id, request, response
            )

    def _prepare_story(self, request: dict[str, Any]) -> dict[str, Any]:
        _require_fields(
            request, {"protocol_version", "request_id", "client_session_id"}
        )
        request_id = _require_request_id(request["request_id"])
        _require_request_id(request["client_session_id"])
        if request["protocol_version"] != PROTOCOL_VERSION:
            raise BridgeError(
                409, "protocol_mismatch", "client protocol version was not supported"
            )
        if self._story_prepare_handler is None:
            raise BridgeError(
                404, "story_preparation_disabled", "story preparation was not enabled"
            )
        with self._cache_lock:
            prior = self._story_prepare_requests.get(request_id)
            if prior is not None:
                return self._idempotent(
                    self._story_prepare_requests, request_id, request, prior[1]
                )
        result = dict(self._story_prepare_handler(dict(request)))
        if set(result) != {
            "world_day",
            "status",
            "opening_seen",
            "shift_phase",
            "last_completed_story_day",
        }:
            raise BridgeError(
                503, "invalid_story_preparation", "story preparation response was invalid"
            )
        if (
            isinstance(result["world_day"], bool)
            or not isinstance(result["world_day"], int)
            or result["world_day"] < 1
            or result["status"] != "ready"
            or not isinstance(result["opening_seen"], bool)
            or result["shift_phase"] != "playing"
            or isinstance(result["last_completed_story_day"], bool)
            or not isinstance(result["last_completed_story_day"], int)
            or result["last_completed_story_day"] < 0
            or result["last_completed_story_day"] >= result["world_day"]
        ):
            raise BridgeError(
                503, "invalid_story_preparation", "story preparation response was invalid"
            )
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            **result,
        }
        with self._cache_lock:
            return self._idempotent(
                self._story_prepare_requests, request_id, request, response
            )


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, config: BridgeConfig, app: BridgeApplication | None = None) -> None:
        self.app = app or BridgeApplication(config)
        super().__init__((config.host, config.port), BridgeRequestHandler)


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            length = -1
        if length < 0:
            response = BridgeResponse(
                400,
                {"error": {"code": "invalid_length", "message": "Content-Length was invalid"}},
            )
        elif length > MAX_REQUEST_BYTES:
            response = BridgeResponse(
                413,
                {
                    "error": {
                        "code": "request_too_large",
                        "message": "request body exceeded size limit",
                    }
                },
            )
        else:
            body = self.rfile.read(length) if length else b""
            response = self.server.app.handle(
                self.command, self.path, dict(self.headers.items()), body
            )
        encoded = _canonical_json(response.body)
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        # Do not let paths, headers or bridge tokens enter ordinary logs.
        return


def serve_bridge(
    config: BridgeConfig, app: BridgeApplication | None = None
) -> None:
    with BridgeHTTPServer(config, app=app) as server:
        server.serve_forever(poll_interval=0.1)
