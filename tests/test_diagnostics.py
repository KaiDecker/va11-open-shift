from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_shift.diagnostics import emit_timing


class DiagnosticsTests(unittest.TestCase):
    def test_timing_event_has_wall_timestamp_and_writes_secret_free_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "timing.log"
            with patch.dict(os.environ, {"OPEN_SHIFT_TIMING_LOG": str(path)}):
                emit_timing("provider_request_end", elapsed_ms=123, thinking="enabled")
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["event"], "provider_request_end")
            self.assertEqual(record["elapsed_ms"], 123)
            self.assertEqual(record["thinking"], "enabled")
            self.assertIn("T", record["timestamp"])
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
