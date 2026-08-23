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
        print("[OPEN SHIFT TIMING] " + line, file=sys.stderr, flush=True)


def monotonic_seconds() -> float:
    """Expose a monotonic clock for elapsed-time measurements."""

    return time.perf_counter()
