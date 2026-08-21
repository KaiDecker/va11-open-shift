from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from open_shift.models import AgentState
from open_shift.paired_saves import (
    ORIGINAL_SAVE_SLOT_COUNT,
    PairedSaveError,
    PairedSaveManager,
    PairedSaveMismatch,
    WorldSessionCheckpoint,
)
from open_shift.store import WorldStore


class PairedSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.live_db = self.root / "world.sqlite3"
        self.native_dir = self.root / "native" / "saves"
        self.snapshot_root = self.root / "paired"
        self.native_dir.mkdir(parents=True)
        with WorldStore(self.live_db) as store:
            store.add_agent(AgentState("dana", "Dana", "home", 90, 0.2, "steady", 480))
            store.set_current_tick(1440)
            store.set_meta("current_story_day", 1)
            store.set_meta("bridge_ack:opening_day_1", 6)
            store.set_meta("player_shift_income", 400)
        self.manager = PairedSaveManager(
            self.live_db,
            self.native_dir,
            self.snapshot_root,
            now=lambda: datetime(2026, 8, 17, 9, 30, tzinfo=UTC),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def native_save(self, slot: int, contents: str = "17/8/2026 17:30:00\n1\n1001\n") -> Path:
        path = self.manager.native_save_path(slot)
        path.write_text(contents, encoding="utf-8")
        return path

    def test_all_original_slots_map_to_fixed_native_filenames(self) -> None:
        self.assertEqual(ORIGINAL_SAVE_SLOT_COUNT, 24)
        self.assertEqual(
            self.manager.native_save_path(1).name,
            "Record of Waifu Wars[1].txt",
        )
        self.assertEqual(
            self.manager.native_save_path(24).name,
            "Record of Waifu Wars[24].txt",
        )
        for invalid in (0, 25, True, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(PairedSaveError):
                self.manager.native_save_path(invalid)  # type: ignore[arg-type]

    def test_sqlite_backup_records_native_hash_and_world_resume_state(self) -> None:
        self.native_save(3)
        record = self.manager.save_slot(3)
        self.assertEqual(record.slot, 3)
        self.assertEqual(record.created_at_utc, "2026-08-17T09:30:00Z")
        self.assertEqual(record.world_tick, 1440)
        self.assertEqual(record.world_day, 1)
        self.assertTrue(record.opening_seen)
        self.assertEqual(record.player_shift_income, 400)
        self.assertEqual(record.shift_phase, "playing")
        self.assertEqual(record.last_completed_story_day, 0)
        self.assertEqual(self.manager.current_record(3), record)

        snapshot = self.snapshot_root / "slot-03" / f"{record.revision}.sqlite3"
        native_copy = (
            self.snapshot_root / "slot-03" / f"{record.revision}.native.txt"
        )
        self.assertEqual(native_copy.read_bytes(), self.manager.native_save_path(3).read_bytes())
        with WorldStore(snapshot) as store:
            self.assertEqual(store.get_meta("paired_save_revision"), record.revision)
            self.assertEqual(store.get_meta("paired_save_slot"), "3")
            self.assertEqual(store.get_agent("dana").money, 90)

    def test_save_required_checkpoint_becomes_playable_inside_snapshot(self) -> None:
        self.native_save(4)
        with WorldStore(self.live_db) as store:
            store.set_meta("current_story_day", 2)
            store.set_meta("last_completed_story_day", 1)
            store.set_meta("shift_phase", "save_required")
        record = self.manager.save_slot(4)
        self.assertEqual(record.world_day, 2)
        self.assertEqual(record.shift_phase, "playing")
        self.assertEqual(record.last_completed_story_day, 1)
        snapshot = self.snapshot_root / "slot-04" / f"{record.revision}.sqlite3"
        with WorldStore(snapshot) as store:
            self.assertEqual(store.get_meta("shift_phase"), "playing")
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.get_meta("shift_phase"), "save_required")

    def test_session_checkpoint_rolls_back_unsaved_progress_and_advances_on_save(self) -> None:
        checkpoint_path = self.root / "session-checkpoint.sqlite3"
        checkpoint = WorldSessionCheckpoint(self.live_db, checkpoint_path)
        checkpoint.begin()
        with WorldStore(self.live_db) as store:
            store.set_meta("current_story_day", 2)
        checkpoint.capture()
        with WorldStore(self.live_db) as store:
            store.set_meta("current_story_day", 4)
        checkpoint.rollback()
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.get_meta("current_story_day"), "2")
        checkpoint.cleanup()
        self.assertFalse(checkpoint_path.exists())

    def test_new_session_database_is_removed_when_never_saved(self) -> None:
        new_db = self.root / "new-session.sqlite3"
        checkpoint = WorldSessionCheckpoint(
            new_db, self.root / "new-session-checkpoint.sqlite3"
        )
        checkpoint.begin()
        with WorldStore(new_db) as store:
            store.set_meta("current_story_day", 3)
        checkpoint.rollback()
        self.assertFalse(new_db.exists())

    def test_abandoned_session_is_recovered_before_next_launch(self) -> None:
        checkpoint_path = self.root / "abandoned.sqlite3"
        first = WorldSessionCheckpoint(self.live_db, checkpoint_path)
        first.begin()
        with WorldStore(self.live_db) as store:
            store.set_meta("current_story_day", 5)
        restarted = WorldSessionCheckpoint(self.live_db, checkpoint_path)
        self.assertTrue(restarted.recover_abandoned_session())
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.get_meta("current_story_day"), "1")
        self.assertFalse(checkpoint_path.exists())
        self.assertFalse(restarted.state_path.exists())

    def test_rollback_keeps_safe_unplayed_story_draft(self) -> None:
        checkpoint_path = self.root / "draft-checkpoint.sqlite3"
        checkpoint = WorldSessionCheckpoint(self.live_db, checkpoint_path)
        checkpoint.begin()
        with WorldStore(self.live_db) as store:
            source_event = store.append_event(
                store.current_tick,
                "public_world_event",
                None,
                payload={"headline": "A quiet test event"},
            )
        checkpoint.capture()
        with WorldStore(self.live_db) as store:
            store._conn.execute(
                """
                INSERT INTO daily_story_graphs(
                    day_index, generation_version, status, source_tick,
                    source_event_ids_json, graph_json, error_code, attempt_count
                ) VALUES(2, 'test-v1', 'ready', 1440, ?, ?, NULL, 1)
                """,
                (json.dumps([source_event]), json.dumps({"entry_node_id": "opening"})),
            )
            store.set_meta("current_story_day", 3)
        checkpoint.rollback()
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.get_meta("current_story_day"), "1")
            graph = store.get_daily_story_graph(2, "test-v1")
            self.assertIsNotNone(graph)
            self.assertEqual(graph["status"], "ready")

    def test_operation_receipts_survive_restart_and_reject_conflicts(self) -> None:
        self.native_save(9)
        request = {
            "protocol_version": 1,
            "request_id": "pair-restart-9",
            "client_session_id": "paired-session-0001",
            "slot": 9,
        }
        first = self.manager.save_slot(
            9, operation_id=request["request_id"], request=request
        )
        restarted = PairedSaveManager(
            self.live_db, self.native_dir, self.snapshot_root
        )
        replay = restarted.save_slot(
            9, operation_id=request["request_id"], request=request
        )
        self.assertEqual(replay, first)
        self.assertEqual(restarted.current_record(9), first)
        with self.assertRaises(PairedSaveError) as conflict:
            restarted.save_slot(
                9,
                operation_id=request["request_id"],
                request={**request, "client_session_id": "different-session"},
            )
        self.assertEqual(conflict.exception.code, "operation_id_conflict")

    def test_restore_replaces_live_world_only_when_native_save_matches(self) -> None:
        self.native_save(7)
        record = self.manager.save_slot(7)
        with WorldStore(self.live_db) as store:
            dana = store.get_agent("dana")
            dana.money = 5
            store.update_agent(dana)
            store.set_current_tick(2880)
            store.set_meta("player_shift_income", 999)

        restored = self.manager.restore_slot(7)
        self.assertEqual(restored, record)
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.get_agent("dana").money, 90)
            self.assertEqual(store.current_tick, 1440)
            self.assertEqual(store.get_meta("player_shift_income"), "400")
            self.assertEqual(store.get_meta("paired_save_revision"), record.revision)

        self.native_save(7, "different native save\n")
        with self.assertRaises(PairedSaveMismatch) as mismatch:
            self.manager.restore_slot(7)
        self.assertEqual(mismatch.exception.code, "native_save_mismatch")
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.current_tick, 1440)

    def test_failed_overwrite_keeps_previous_complete_slot_pointer(self) -> None:
        self.native_save(12)
        original_native = self.manager.native_save_path(12).read_bytes()
        first = self.manager.save_slot(12)
        with WorldStore(self.live_db) as store:
            store.set_current_tick(2880)
        self.native_save(12, "17/8/2026 18:00:00\n1\n1001\n")

        with patch.object(
            self.manager,
            "_write_current_pointer",
            side_effect=OSError("synthetic pointer failure"),
        ):
            with self.assertRaises(PairedSaveError) as failure:
                self.manager.save_slot(12)
        self.assertEqual(failure.exception.layer, "snapshot")
        self.assertEqual(self.manager.current_record(12), first)
        self.assertEqual(self.manager.native_save_path(12).read_bytes(), original_native)

        pointer = json.loads(
            (self.snapshot_root / "slot-12" / "current.json").read_text(encoding="utf-8")
        )
        self.assertEqual(pointer["revision"], first.revision)

    def test_corrupt_snapshot_is_rejected_without_changing_live_world(self) -> None:
        self.native_save(24)
        record = self.manager.save_slot(24)
        snapshot = self.snapshot_root / "slot-24" / f"{record.revision}.sqlite3"
        with snapshot.open("ab") as stream:
            stream.write(b"corrupt")
        with WorldStore(self.live_db) as store:
            store.set_current_tick(4320)

        with self.assertRaises(PairedSaveMismatch) as mismatch:
            self.manager.restore_slot(24)
        self.assertEqual(mismatch.exception.code, "snapshot_mismatch")
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.current_tick, 4320)

    def test_failed_restore_rolls_live_world_back_to_pre_restore_state(self) -> None:
        self.native_save(18)
        self.manager.save_slot(18)
        with WorldStore(self.live_db) as store:
            dana = store.get_agent("dana")
            dana.money = 333
            store.update_agent(dana)
            store.set_current_tick(5760)

        def fail_after_partial_restore(_snapshot: Path) -> None:
            with WorldStore(self.live_db) as store:
                dana = store.get_agent("dana")
                dana.money = 1
                store.update_agent(dana)
            raise OSError("synthetic restore failure")

        with patch.object(
            self.manager,
            "_restore_live_from",
            side_effect=fail_after_partial_restore,
        ):
            with self.assertRaises(PairedSaveError) as failure:
                self.manager.restore_slot(18)
        self.assertEqual(failure.exception.layer, "restore")
        with WorldStore(self.live_db) as store:
            self.assertEqual(store.get_agent("dana").money, 333)
            self.assertEqual(store.current_tick, 5760)


if __name__ == "__main__":
    unittest.main()
