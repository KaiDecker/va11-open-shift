"""Small timestamped diagnostics sink for player-facing runtime debugging."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()


def emit_dialogue_transcript(
    story_day: int,
    scene_id: str,
    lines: list[dict[str, str]],
) -> None:
    """Append the displayed scene transcript without prompts or secrets.

    This is deliberately separate from timing.log: a player can share the
    dialogue trace while keeping provider credentials and request payloads
    private.  The bridge calls it only for the first acknowledgement of a
    scene, so retries cannot duplicate a transcript.
    """

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": "dialogue_transcript",
        "story_day": story_day,
        "scene_id": scene_id,
        "lines": lines,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with _LOCK:
        path_value = os.environ.get("OPEN_SHIFT_DIALOGUE_LOG", "").strip()
        if not path_value:
            return
        try:
            path = Path(path_value)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        except OSError:
            # Diagnostics must never break gameplay.
            pass


def emit_timing(event: str, **fields: Any) -> None:
    """Write one secret-free timestamped timing event.

    The optional ``OPEN_SHIFT_TIMING_LOG`` path is set by installed launchers.
    stderr remains useful for development and is intentionally free of request
    headers, API keys, prompts, and response bodies.
    """

    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **fields,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with _LOCK:
        path_value = os.environ.get("OPEN_SHIFT_TIMING_LOG", "").strip()
        if path_value:
            try:
                path = Path(path_value)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
            except OSError:
                # Diagnostics must never break gameplay.
                pass
        # Installed launches set a log path, so keep the live console useful.
        # Library/tests without a log path stay quiet unless explicitly asked.
        stderr_enabled = os.environ.get("OPEN_SHIFT_TIMING_STDERR", "").strip().lower()
        if path_value or stderr_enabled in {"1", "true", "yes", "on"}:
            print("[OPEN SHIFT TIMING] " + line, file=sys.stderr, flush=True)


def monotonic_seconds() -> float:
    """Expose a monotonic clock for elapsed-time measurements."""

    return time.perf_counter()
