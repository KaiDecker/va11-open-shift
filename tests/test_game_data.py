from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from open_shift.game_data import (
    GameDataError,
    compare_inventories,
    inspect_game_data,
    inventory_json,
)


def game_data(*, names: tuple[str, ...], code: bytes = b"code") -> bytes:
    string_payload = b"\0".join(name.encode("ascii") for name in names) + b"\0"
    chunks = b"CODE" + struct.pack("<I", len(code)) + code
    chunks += b"STRG" + struct.pack("<I", len(string_payload)) + string_payload
    return b"FORM" + struct.pack("<I", len(chunks)) + chunks


class GameDataInventoryTests(unittest.TestCase):
    def test_inventory_is_names_only_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.win"
            payload = game_data(
                names=("sprite_dana", "gml_Object_extrachapters_Create_0", "not a name")
            )
            path.write_bytes(payload)
            first = inspect_game_data(path)
            second = inspect_game_data(path)
            self.assertEqual(first, second)
            self.assertEqual(first.sha256, hashlib.sha256(payload).hexdigest())
            self.assertEqual(
                first.resource_names,
                ("gml_Object_extrachapters_Create_0", "sprite_dana"),
            )
            serialized = json.loads(inventory_json(first))
            self.assertEqual(
                set(serialized),
                {
                    "path",
                    "file_size",
                    "sha256",
                    "supported_original",
                    "chunks",
                    "resource_names",
                },
            )
            self.assertTrue(
                all(set(chunk) == {"name", "offset", "size"} for chunk in serialized["chunks"])
            )
            self.assertFalse(first.supported_original)

    def test_compare_reports_chunk_and_resource_name_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_path = Path(temp_dir) / "baseline.win"
            reference_path = Path(temp_dir) / "reference.win"
            baseline_path.write_bytes(game_data(names=("existing",), code=b"a"))
            reference_path.write_bytes(
                game_data(names=("existing", "new_resource"), code=b"longer")
            )
            comparison = compare_inventories(
                inspect_game_data(baseline_path), inspect_game_data(reference_path)
            )
            self.assertEqual(comparison["added_resource_names"], ["new_resource"])
            self.assertEqual(comparison["removed_resource_names"], [])
            self.assertEqual(comparison["chunk_size_deltas"]["CODE"], 5)

    def test_malformed_or_truncated_container_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.win"
            path.write_bytes(b"not a data file")
            with self.assertRaises(GameDataError):
                inspect_game_data(path)
            path.write_bytes(b"FORM" + struct.pack("<I", 100) + b"STRG")
            with self.assertRaises(GameDataError):
                inspect_game_data(path)


if __name__ == "__main__":
    unittest.main()
