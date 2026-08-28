from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import unittest
from pathlib import Path

from open_shift.data_delta import DataDeltaError, apply_delta, create_delta


def form(*chunks: tuple[bytes, bytes]) -> bytes:
    body = b"".join(name + struct.pack("<I", len(payload)) + payload for name, payload in chunks)
    return b"FORM" + struct.pack("<I", len(body)) + body


class DataDeltaTests(unittest.TestCase):
    def test_content_defined_spans_reuse_data_after_insertion(self) -> None:
        shared = (b"stable bytes that should survive a shifted offset\n" * 12000)
        original = form((b"GEN8", b"header"), (b"CODE", shared), (b"STRG", b"tail"))
        patched = form((b"GEN8", b"header"), (b"CODE", b"new prefix\n" * 300 + shared), (b"STRG", b"tail"))
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            original_path = root / "original.data.win"
            patched_path = root / "patched.data.win"
            delta_path = root / "data-win.delta"
            output_path = root / "output.data.win"
            original_path.write_bytes(original)
            patched_path.write_bytes(patched)
            info = create_delta(original_path, patched_path, delta_path, max_ratio=10.0)
            apply_delta(original_path, delta_path, output_path)
            self.assertEqual(output_path.read_bytes(), patched)
            self.assertGreater(info["copied_chunks"], 0)
            self.assertLess(info["embedded_bytes"], len(patched) // 2)

    def test_round_trip_reuses_unchanged_chunks(self) -> None:
        original = form((b"GEN8", b"header"), (b"STRG", b"old strings"), (b"CODE", b"unchanged" * 300))
        patched = form((b"GEN8", b"header"), (b"STRG", b"new strings"), (b"CODE", b"unchanged" * 300), (b"ROOM", b"new room"))
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            original_path = root / "original.data.win"
            patched_path = root / "patched.data.win"
            delta_path = root / "data-win.delta"
            output_path = root / "output.data.win"
            original_path.write_bytes(original)
            patched_path.write_bytes(patched)
            info = create_delta(original_path, patched_path, delta_path, max_ratio=10.0)
            result = apply_delta(original_path, delta_path, output_path)
            self.assertEqual(output_path.read_bytes(), patched)
            self.assertEqual(result["patched_sha256"], hashlib.sha256(patched).hexdigest())
            self.assertLess(info["embedded_bytes"], len(patched))

    def test_rejects_wrong_original_without_writing_output(self) -> None:
        original = form((b"GEN8", b"header"), (b"STRG", b"strings"))
        patched = form((b"GEN8", b"header"), (b"STRG", b"changed"))
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            original_path = root / "original.data.win"
            patched_path = root / "patched.data.win"
            delta_path = root / "data-win.delta"
            output_path = root / "output.data.win"
            original_path.write_bytes(original)
            patched_path.write_bytes(patched)
            create_delta(original_path, patched_path, delta_path, max_ratio=10.0)
            original_path.write_bytes(form((b"GEN8", b"tampered"), (b"STRG", b"strings")))
            with self.assertRaisesRegex(DataDeltaError, "baseline"):
                apply_delta(original_path, delta_path, output_path)
            self.assertFalse(output_path.exists())

    def test_rejects_near_full_delta(self) -> None:
        original = form((b"GEN8", os.urandom(20_000)), (b"STRG", os.urandom(20_000)))
        patched = form((b"GEN8", os.urandom(20_000)), (b"STRG", os.urandom(20_000)))
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            original_path = root / "original.data.win"
            patched_path = root / "patched.data.win"
            original_path.write_bytes(original)
            patched_path.write_bytes(patched)
            with self.assertRaisesRegex(DataDeltaError, "too large"):
                create_delta(original_path, patched_path, root / "data-win.delta")


if __name__ == "__main__":
    unittest.main()
