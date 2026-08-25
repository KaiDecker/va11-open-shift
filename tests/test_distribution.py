from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from open_shift.distribution import (
    DistributionError,
    install_patch,
    uninstall_patch,
    verify_patch_output,
)
from open_shift.game_data import inspect_game_data
from open_shift.patch_contract import PatchManifest


def game_data(*, names: tuple[str, ...], code: bytes = b"code") -> bytes:
    strings = b"\0".join(name.encode("ascii") for name in names) + b"\0"
    chunks = b"CODE" + struct.pack("<I", len(code)) + code
    chunks += b"STRG" + struct.pack("<I", len(strings)) + strings
    return b"FORM" + struct.pack("<I", len(chunks)) + chunks


class DistributionTests(unittest.TestCase):
    def manifest_for(self, original: Path) -> PatchManifest:
        inventory = inspect_game_data(original)
        return PatchManifest(
            mod_id="open_shift",
            protocol_version=1,
            supported_originals=(
                {
                    "data_win_sha256": inventory.sha256,
                    "data_win_size": inventory.file_size,
                },
            ),
            required_resources=("existing",),
            new_resources=("new_resource",),
            allowed_portraits={"jill": None},
            return_target="bar",
        )

    def test_install_backups_existing_copy_and_uninstall_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "original.win"
            patched = root / "patched.win"
            destination = root / "copy" / "data.win"
            backup_dir = root / "backups"
            record = root / "install.json"
            original.write_bytes(game_data(names=("existing",), code=b"original"))
            patched.write_bytes(game_data(names=("existing", "new_resource"), code=b"patched"))
            old_copy = game_data(names=("existing",), code=b"old-copy")
            destination.parent.mkdir()
            destination.write_bytes(old_copy)

            installed = install_patch(
                original_data_win=original,
                patched_data_win=patched,
                destination_data_win=destination,
                backup_dir=backup_dir,
                manifest=self.manifest_for(original),
                record_path=record,
            )
            self.assertEqual(destination.read_bytes(), patched.read_bytes())
            self.assertTrue(Path(installed.backup_path).is_file())
            self.assertTrue(record.is_file())
            restored = uninstall_patch(record_path=record)
            self.assertEqual(restored.installed_data_win, str(destination.resolve()))
            self.assertEqual(destination.read_bytes(), old_copy)
            self.assertFalse(record.exists())

    def test_install_refuses_original_destination_and_missing_patch_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "original.win"
            patched = root / "patched.win"
            original.write_bytes(game_data(names=("existing",)))
            patched.write_bytes(game_data(names=("existing",), code=b"different"))
            manifest = self.manifest_for(original)
            with self.assertRaisesRegex(DistributionError, "over the verified original"):
                install_patch(
                    original_data_win=original,
                    patched_data_win=patched,
                    destination_data_win=original,
                    backup_dir=root / "backups",
                    manifest=manifest,
                )
            with self.assertRaisesRegex(DistributionError, "missing new resources"):
                install_patch(
                    original_data_win=original,
                    patched_data_win=patched,
                    destination_data_win=root / "copy.win",
                    backup_dir=root / "backups",
                    manifest=manifest,
                )

    def test_uninstall_refuses_changed_installed_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "original.win"
            patched = root / "patched.win"
            destination = root / "copy.win"
            record = root / "install.json"
            original.write_bytes(game_data(names=("existing",), code=b"original"))
            patched.write_bytes(game_data(names=("existing", "new_resource"), code=b"patched"))
            destination.write_bytes(game_data(names=("existing",), code=b"old-copy"))
            install_patch(
                original_data_win=original,
                patched_data_win=patched,
                destination_data_win=destination,
                backup_dir=root / "backups",
                manifest=self.manifest_for(original),
                record_path=record,
            )
            destination.write_bytes(game_data(names=("existing", "new_resource"), code=b"tampered"))
            with self.assertRaisesRegex(DistributionError, "changed after installation"):
                uninstall_patch(record_path=record)

    def test_release_candidate_verification_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "original.win"
            patched = root / "patched.win"
            original.write_bytes(game_data(names=("existing",), code=b"original"))
            patched.write_bytes(
                game_data(names=("existing", "new_resource"), code=b"patched")
            )
            result = verify_patch_output(
                original_data_win=original,
                patched_data_win=patched,
                manifest=self.manifest_for(original),
                gml_source_dir=Path(__file__).resolve().parents[1]
                / "game-patch"
                / "gml",
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["gml_source_count"], 34)
            self.assertEqual(original.read_bytes(), game_data(names=("existing",), code=b"original"))


if __name__ == "__main__":
    unittest.main()
