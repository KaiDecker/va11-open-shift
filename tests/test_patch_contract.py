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
                    "allowed_portraits": {"sprite_dana": "sprite_dana"},
                    "return_target": "bar",
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
        self.assertEqual(len(sources), 16)

    def test_menu_entry_matches_reference_chapter_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        patch = (root / "game-patch" / "apply_mod.csx").read_text(encoding="utf-8")
        chapter = (root / "game-patch" / "gml" / "ag_open_shift_chapter_step.gml").read_text(
            encoding="utf-8"
        )
        start = (root / "game-patch" / "gml" / "ag_open_shift_start_step.gml").read_text(
            encoding="utf-8"
        )
        controller = (root / "game-patch" / "gml" / "ag_bridge_controller_step.gml").read_text(
            encoding="utf-8"
        )
        controller_http = (
            root / "game-patch" / "gml" / "ag_bridge_controller_http.gml"
        ).read_text(encoding="utf-8")
        controller_create = (
            root / "game-patch" / "gml" / "ag_bridge_controller_create.gml"
        ).read_text(encoding="utf-8")
        mixcontrol = (
            root / "game-patch" / "gml" / "ag_bridge_mixcontrol_append.gml"
        ).read_text(encoding="utf-8")
        save_http = (
            root / "game-patch" / "gml" / "ag_save_controller_http.gml"
        ).read_text(encoding="utf-8")
        save_flow = (
            root / "game-patch" / "gml" / "ag_save_flow_controller_step.gml"
        ).read_text(encoding="utf-8")
        save_flow_create = (
            root / "game-patch" / "gml" / "ag_save_flow_controller_create.gml"
        ).read_text(encoding="utf-8")
        load_slot = (
            root / "game-patch" / "gml" / "ag_load_slot_script.gml"
        ).read_text(encoding="utf-8")
        towork = (
            root / "game-patch" / "gml" / "ag_towork_button_mouse.gml"
        ).read_text(encoding="utf-8")

        self.assertIn('Data.Sprites.ByName("blue_chapter")', patch)
        self.assertIn('Data.Sprites.ByName("yellow_chapter")', patch)
        self.assertIn('Data.Code.ByName("gml_Object_prologuechapter_Step_0")', patch)
        self.assertNotIn("instance_create(254, 318", patch)
        self.assertIn("annachapter.y + 14", chapter)
        self.assertIn("ag_open_shift_chapter.y + 14", start)
        self.assertIn("cursor_hitbox", chapter)
        self.assertIn("cursor_hitbox", start)
        self.assertIn("out_of_apartment", start)
        self.assertIn('Data.Code.ByName("gml_Object_extrachapter_text_Draw_0")', patch)
        self.assertIn("dialogfont2", patch)
        self.assertIn("ch_small", patch)
        self.assertIn('Data.Code.ByName("gml_Object_dialog_control_Create_0")', patch)
        self.assertIn('Data.Code.ByName("gml_Script_mixcontrol")', patch)
        self.assertIn("obj_textbox", controller)
        self.assertIn("sprite_stella", controller)
        self.assertIn("ag_name_color", controller)
        self.assertIn("ds_queue_enqueue", controller)
        self.assertIn("draw_set_font(global.fnt_textbox)", controller)
        self.assertIn("string_width(ag_wrap_candidate) > 380", controller)
        self.assertIn('ag_wrapped_text += "#"', controller)
        self.assertIn("string_length(ag_line_text) > 72", controller_http)
        self.assertNotIn("ag_safe_text", patch)
        self.assertNotIn("draw_rectangle", controller)
        self.assertNotIn("out_to_title", controller)
        self.assertIn('"continued_in_bar"', controller)
        self.assertIn("ds_list_size(ag_lines) < 1", controller_http)
        self.assertIn("ds_list_size(ag_lines) > 8", controller_http)
        self.assertIn("ag_line_count = ds_list_size(ag_lines)", controller_http)
        self.assertIn("current_time + 120000", controller_create)
        self.assertIn("current_time + 120000", controller_http)
        self.assertIn("冰箱压缩机在吧台后低声运转", controller)
        self.assertNotIn("正在准备下一段对话", controller)
        self.assertIn("API调用额度已用完", controller_http)
        self.assertIn('ag_speaker_id != "jill"', controller_http)
        self.assertIn('ag_portrait_id != ""', controller_http)
        self.assertNotIn("is_undefined(ag_portrait_id)", controller_http)
        self.assertIn('ag_current_speaker != "jill"', controller)
        self.assertIn('ag_speaker_id == ""', controller_http)
        self.assertIn("story_generation_failed", controller_http)
        self.assertIn("income_delta", controller_http)
        self.assertIn("global.cashcounter += ag_income_delta", controller_http)
        self.assertIn("global.barscore += ag_income_delta", controller_http)
        self.assertIn("global.jillwallet += global.cashcounter", controller_http)
        self.assertIn('global.datestring = "O.S. DAY "', controller_http)
        self.assertIn('string_delete(ag_completed_day, 1, 4)', controller_http)
        self.assertNotIn("global.money", controller_http)
        self.assertNotIn('"ini_close", "is_undefined"', patch)
        self.assertIn("ag_was_order_response = (ag_state == 7)", controller_http)
        self.assertIn("else if (ag_was_order_response)", controller_http)
        self.assertIn("resetmixer_2()", controller_http)
        self.assertIn('"order_started"', controller)
        self.assertIn('"/v1/orders/resolve"', mixcontrol)
        self.assertIn("global.mod_aa", mixcontrol)
        self.assertIn("global.failed_a", mixcontrol)
        self.assertNotIn("claimed_result", mixcontrol)
        self.assertIn('ag_expected_status = "paired"', save_http)
        self.assertIn('ag_expected_status = "restored"', save_http)
        self.assertNotIn('ag_operation + "ed"', save_http)
        self.assertIn("jill_room", save_flow)
        self.assertIn("out_of_apartment", save_flow)
        self.assertNotIn("instance_create(room_width / 2, 165, save_home)", save_flow)
        self.assertNotIn("global.cur_data = \"save\"", save_flow)
        self.assertNotIn("global.block_click = 1", save_flow_create)
        self.assertIn('ag_operation = "restore"', load_slot)
        self.assertIn('Data.Code.ByName("gml_Script_load_slot_script")', patch)
        self.assertIn('Name = Data.Strings.MakeString("ag_save_controller")', patch)
        self.assertIn('Name = Data.Strings.MakeString("ag_save_flow_controller")', patch)
        self.assertIn("for (int slot = 1; slot <= 24; slot++)", patch)
        self.assertIn("data_icon.alarm[0] = 10", towork)
        self.assertIn("data_icon.chosen = 1", towork)
        self.assertIn('Data.Code.ByName("gml_Object_towork_button_Mouse_4")', patch)

    def test_incomplete_gml_source_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "ag_bridge_controller_step.gml").write_text(
                "instance_create(0, 0, obj_textbox);", encoding="utf-8"
            )
            with self.assertRaisesRegex(PatchContractError, "did not match"):
                validate_patch_source_tree(temp_dir)

    def test_nonexistent_original_money_variable_is_rejected(self) -> None:
        root = Path(__file__).resolve().parents[1] / "game-patch" / "gml"
        with tempfile.TemporaryDirectory() as temp_dir:
            copy = Path(temp_dir)
            for source in root.glob("*.gml"):
                text = source.read_text(encoding="utf-8")
                if source.name == "ag_bridge_controller_http.gml":
                    text = text.replace(
                        "global.cashcounter += ag_income_delta",
                        "global.money += ag_income_delta",
                    )
                (copy / source.name).write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(PatchContractError, "money variable"):
                validate_patch_source_tree(copy)

    def test_legacy_jill_null_check_is_rejected(self) -> None:
        root = Path(__file__).resolve().parents[1] / "game-patch" / "gml"
        with tempfile.TemporaryDirectory() as temp_dir:
            copy = Path(temp_dir)
            for source in root.glob("*.gml"):
                text = source.read_text(encoding="utf-8")
                if source.name == "ag_bridge_controller_http.gml":
                    text = text.replace(
                        'ag_portrait_id != ""',
                        "!is_undefined(ag_portrait_id)",
                    )
                (copy / source.name).write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(PatchContractError, "null portrait"):
                validate_patch_source_tree(copy)


if __name__ == "__main__":
    unittest.main()
