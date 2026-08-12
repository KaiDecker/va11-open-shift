from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from open_shift.game_data import GameDataChunk, GameDataInventory
from open_shift.patch_contract import (
    PatchContractError,
    load_patch_manifest,
    validate_gml_safety,
    validate_patch_target,
)


def inventory(*, digest: str, names: tuple[str, ...]) -> GameDataInventory:
    return GameDataInventory(
        path="C:/test/data.win",
        file_size=100,
        sha256=digest,
        supported_original=False,
        chunks=(GameDataChunk("STRG", 8, 10),),
        resource_names=names,
    )


class PatchContractTests(unittest.TestCase):
    def write_manifest(self, directory: str) -> Path:
        path = Path(directory) / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "mod_id": "open_shift",
                    "protocol_version": 1,
                    "supported_originals": [
                        {
                            "platform": "test",
                            "data_win_size": 100,
                            "data_win_sha256": "abc",
                            "executable_name": "game.exe",
                            "executable_sha256": "def",
                        }
                    ],
                    "required_resources": ["extrachapters", "sprite_dana"],
                    "new_resources": ["ag_bridge_controller"],
                    "allowed_portraits": {"none": None, "sprite_dana": "sprite_dana"},
                    "return_target": "title",
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_supported_target_with_required_names_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = load_patch_manifest(self.write_manifest(temp_dir))
            validate_patch_target(
                manifest,
                inventory(digest="abc", names=("extrachapters", "sprite_dana")),
            )

    def test_wrong_hash_missing_resource_and_collision_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = load_patch_manifest(self.write_manifest(temp_dir))
            with self.assertRaisesRegex(PatchContractError, "supported original"):
                validate_patch_target(
                    manifest,
                    inventory(digest="wrong", names=("extrachapters", "sprite_dana")),
                )
            with self.assertRaisesRegex(PatchContractError, "missing"):
                validate_patch_target(
                    manifest, inventory(digest="abc", names=("extrachapters",))
                )
            with self.assertRaisesRegex(PatchContractError, "already existed"):
                validate_patch_target(
                    manifest,
                    inventory(
                        digest="abc",
                        names=(
                            "extrachapters",
                            "sprite_dana",
                            "ag_bridge_controller",
                        ),
                    ),
                )

    def test_gml_safety_rejects_dynamic_code_and_destructive_capabilities(self) -> None:
        validate_gml_safety('draw_text(0, 0, "[XS:literal]");')
        with self.assertRaisesRegex(PatchContractError, "execute_string"):
            validate_gml_safety("execute_string(generated_text);")
        with self.assertRaisesRegex(PatchContractError, "file_delete"):
            validate_gml_safety("file_delete(path);")


if __name__ == "__main__":
    unittest.main()
