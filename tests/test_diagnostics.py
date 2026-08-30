from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO

from open_shift.diagnostics import emit_dialogue_transcript, emit_timing
from open_shift.cli import main
from open_shift.models import AgentState
from open_shift.store import WorldStore
from open_shift.world_diagnostics import inspect_world_database


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

    def test_world_report_is_read_only_and_groups_events_transcripts_memories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "world.sqlite3"
            with WorldStore(database) as store:
                store.add_agent(AgentState("alma", "Alma", "bar", 10, 0.2, "calm", 480))
                event_id = store.append_event(
                    1_450,
                    "dialogue_transcript",
                    None,
                    payload={
                        "story_day": 2,
                        "scene_id": "opening_day_2",
                        "lines": [{"speaker_id": "alma", "text": "线路有变化。"}],
                        "prompt": "must never be exported",
                    },
                )
                store.append_memory(
                    "alma", event_id, 0.8, "Alma heard about the route change.",
                    ("city",), source_type="heard",
                )
                store.set_meta("current_story_day", 3)
                store.set_meta(
                    "llm_public_event_attempt:2",
                    json.dumps({"status": "ready", "api_key": "do-not-export"}),
                )
                store.set_meta("bridge_scene_payload:opening_day_2", "private scene payload")

            before = database.read_bytes()
            report = inspect_world_database(database, day=2)
            self.assertEqual(database.read_bytes(), before)
            self.assertEqual(report["days"], [2])
            self.assertEqual(len(report["events"]), 1)
            self.assertEqual(report["dialogue_transcripts"][0]["scene_id"], "opening_day_2")
            self.assertEqual(report["memories"][0]["agent_id"], "alma")
            self.assertEqual(report["generation"]["metadata"]["llm_public_event_attempt:2"]["api_key"], "[redacted]")
            self.assertNotIn("bridge_scene_payload", report["generation"]["metadata"])
            self.assertNotIn("must never be exported", json.dumps(report, ensure_ascii=False))

    def test_world_report_reads_optional_logs_and_cli_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "world.sqlite3"
            with WorldStore(database):
                pass
            timing = root / "timing.log"
            timing.write_text(
                json.dumps({"event": "background_generation_ready", "day": 3}) + "\n"
                + "not-json\n", encoding="utf-8"
            )
            dialogue = root / "dialogue.log"
            dialogue.write_text(
                json.dumps({"event": "dialogue_transcript", "story_day": 3, "lines": []}) + "\n",
                encoding="utf-8",
            )
            report = inspect_world_database(database, day=3, timing_log=timing, dialogue_log=dialogue)
            self.assertEqual(report["logs"]["timing"]["records"][0]["event"], "background_generation_ready")
            self.assertEqual(report["logs"]["timing"]["malformed_lines"], 1)
            self.assertEqual(len(report["logs"]["dialogue"]["records"]), 1)

            from io import StringIO
            from unittest.mock import patch
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                self.assertEqual(main(["diagnose-world", "--db", str(database)]), 0)
            cli_report = json.loads(stdout.getvalue())
            self.assertEqual(cli_report["report_version"], 1)


if __name__ == "__main__":
    unittest.main()
