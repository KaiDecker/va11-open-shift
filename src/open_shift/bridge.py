"""Loopback-only HTTP bridge between GameMaker and the world service."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_TEXT_CHARACTERS = 240
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

ALLOWED_SPEAKERS = frozenset({"jill", "dana", "dorothy", "alma", "stella", "sei"})
ALLOWED_PORTRAITS = frozenset({"none", "sprite_dana", "sprite_doro"})
ALLOWED_EXPRESSIONS = frozenset({"neutral", "happy", "worry", "playful"})
ALLOWED_RETURN_TARGETS = frozenset({"title"})


class BridgeError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SceneLine:
    line_id: str
    speaker_id: str
    portrait_id: str
    expression_id: str
    text: str

    def __post_init__(self) -> None:
        if not _RESOURCE_ID.fullmatch(self.line_id):
            raise ValueError("line_id was invalid")
        if self.speaker_id not in ALLOWED_SPEAKERS:
            raise ValueError("speaker_id was not allowed")
        if self.portrait_id not in ALLOWED_PORTRAITS:
            raise ValueError("portrait_id was not allowed")
        if self.expression_id not in ALLOWED_EXPRESSIONS:
            raise ValueError("expression_id was not allowed")
        if not self.text or len(self.text) > MAX_TEXT_CHARACTERS:
            raise ValueError("scene text length was invalid")
        if any(ord(character) < 32 for character in self.text):
            raise ValueError("scene text contained a control character")

    def to_dict(self) -> dict[str, str]:
        return {
            "line_id": self.line_id,
            "speaker_id": self.speaker_id,
            "portrait_id": self.portrait_id,
            "expression_id": self.expression_id,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ScenePackage:
    scene_id: str
    lines: tuple[SceneLine, ...]
    return_to: str = "title"

    def __post_init__(self) -> None:
        if not _RESOURCE_ID.fullmatch(self.scene_id):
            raise ValueError("scene_id was invalid")
        if not 1 <= len(self.lines) <= 20:
            raise ValueError("scene must contain between 1 and 20 lines")
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("scene line identifiers must be unique")
        if self.return_to not in ALLOWED_RETURN_TARGETS:
            raise ValueError("return target was not allowed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "lines": [line.to_dict() for line in self.lines],
            "return_to": self.return_to,
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
                "jill",
                "none",
                "neutral",
                "场景文本正在作为普通字符安全显示。",
            ),
            SceneLine(
                "connection_3",
                "dana",
                "sprite_dana",
                "neutral",
                "测试结束后将安全返回标题画面。",
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
        ack_handler: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.scene = scene or stage_three_scene()
        self._scene_provider = scene_provider
        self._ack_handler = ack_handler
        self._open_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        self._ack_requests: dict[str, tuple[str, dict[str, Any]]] = {}
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
            if method == "POST" and route == "/v1/scenes/open":
                try:
                    return BridgeResponse(200, self._open_scene(_require_object(body)))
                except BridgeError:
                    raise
                except Exception:
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
                except Exception:
                    raise BridgeError(
                        503,
                        "scene_ack_unavailable",
                        "the world service could not record the scene result",
                    ) from None
            raise BridgeError(404, "not_found", "route was not found")
        except BridgeError as error:
            return BridgeResponse(
                error.status,
                {"error": {"code": error.code, "message": error.message}},
            )

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
                "scene": scene.to_dict(),
            }
            return self._idempotent(
                self._open_requests, request_id, request, response
            )

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
        if request["outcome"] != "returned_to_title":
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
