from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO

from open_shift.diagnostics import emit_dialogue_transcript, emit_timing


class DiagnosticsTests(unittest.TestCase):
    def test_timing_without_log_path_is_quiet_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr", new_callable=StringIO) as stderr:
            emit_timing("memory_retrieval_selected", selected_count=0)
        self.assertEqual(stderr.getvalue(), "")

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

    def test_dialogue_transcript_writes_full_lines_without_prompt_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dialogue.log"
            lines = [{"line_id": "dialogue_1", "speaker_id": "alma", "text": "交通线路改了。"}]
            with patch.dict(os.environ, {"OPEN_SHIFT_DIALOGUE_LOG": str(path)}):
                emit_dialogue_transcript(2, "day_2_customer_1_order", lines)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["event"], "dialogue_transcript")
            self.assertEqual(record["story_day"], 2)
            self.assertEqual(record["lines"], lines)
            self.assertNotIn("prompt", path.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
