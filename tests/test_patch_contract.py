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
    validate_patch_source_tree,
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

    def test_committed_gml_source_tree_has_all_safety_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sources = validate_patch_source_tree(root / "game-patch" / "gml")
        self.assertEqual(len(sources), 11)

    def test_menu_entry_matches_reference_chapter_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        patch = (root / "game-patch" / "apply_mod.csx").read_text(encoding="utf-8")
        chapter = (root / "game-patch" / "gml" / "ag_open_shift_chapter_step.gml").read_text(
            encoding="utf-8"
        )
        start = (root / "game-patch" / "gml" / "ag_open_shift_start_step.gml").read_text(
            encoding="utf-8"
        )
        dialogue = (root / "game-patch" / "gml" / "ag_safe_text_draw.gml").read_text(
            encoding="utf-8"
        )

        self.assertIn('Data.Sprites.ByName("blue_chapter")', patch)
        self.assertIn('Data.Sprites.ByName("yellow_chapter")', patch)
        self.assertIn('Data.Code.ByName("gml_Object_prologuechapter_Step_0")', patch)
        self.assertNotIn("instance_create(254, 318", patch)
        self.assertIn("annachapter.y + 14", chapter)
        self.assertIn("ag_open_shift_chapter.y + 14", start)
        self.assertIn("cursor_hitbox", chapter)
        self.assertIn("cursor_hitbox", start)
        self.assertIn("draw_rectangle(16, 205, 624, 350", dialogue)
        self.assertNotIn("draw_rectangle(16, 320, 624, 464", dialogue)

    def test_incomplete_gml_source_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "ag_safe_text_draw.gml").write_text(
                "draw_text_ext(0, 0, text, 20, 100);", encoding="utf-8"
            )
            with self.assertRaisesRegex(PatchContractError, "did not match"):
                validate_patch_source_tree(temp_dir)


if __name__ == "__main__":
    unittest.main()
