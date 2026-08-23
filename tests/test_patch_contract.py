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
        self.assertEqual(len(sources), 30)

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
        preload_create = (
            root / "game-patch" / "gml" / "ag_preload_controller_create.gml"
        ).read_text(encoding="utf-8")
        preload_step = (
            root / "game-patch" / "gml" / "ag_preload_controller_step.gml"
        ).read_text(encoding="utf-8")
        preload_draw = (
            root / "game-patch" / "gml" / "ag_preload_controller_draw.gml"
        ).read_text(encoding="utf-8")
        popup_room_create = (
            root / "game-patch" / "gml" / "ag_popup_room_create_append.gml"
        ).read_text(encoding="utf-8")
        popup_room_step = (
            root / "game-patch" / "gml" / "ag_popup_room_step.gml"
        ).read_text(encoding="utf-8")
        preload_http = (
            root / "game-patch" / "gml" / "ag_preload_controller_http.gml"
        ).read_text(encoding="utf-8")
        tablet_create = (
            root / "game-patch" / "gml" / "ag_tablet_controller_create.gml"
        ).read_text(encoding="utf-8")
        show_room = (
            root / "game-patch" / "gml" / "ag_show_room_create_append.gml"
        ).read_text(encoding="utf-8")
        variable_create = (
            root / "game-patch" / "gml" / "ag_var_controller_create_append.gml"
        ).read_text(encoding="utf-8")
        new_day = (
            root / "game-patch" / "gml" / "ag_new_day_step_append.gml"
        ).read_text(encoding="utf-8")
        aa_button_1 = (
            root / "game-patch" / "gml" / "ag_aa_button_1_step.gml"
        ).read_text(encoding="utf-8")

        self.assertIn('Data.Sprites.ByName("blue_chapter")', patch)
        self.assertIn('Data.Sprites.ByName("yellow_chapter")', patch)
        self.assertIn('Data.Code.ByName("gml_Object_prologuechapter_Step_0")', patch)
        self.assertNotIn("instance_create(254, 318", patch)
        self.assertIn("annachapter.y + 14", chapter)
        self.assertIn("ag_open_shift_chapter.y + 14", start)
        self.assertIn("cursor_hitbox", chapter)
        self.assertIn("cursor_hitbox", start)
        self.assertIn("room_goto(jill_room)", start)
        self.assertNotIn("ag_preload_controller", start)
        self.assertNotIn("out_of_apartment", preload_step)
        self.assertIn('Data.Code.ByName("gml_Object_extrachapter_text_Draw_0")', patch)
        self.assertIn("dialogfont2", patch)
        self.assertIn("ch_small", patch)
        self.assertIn('Data.Code.ByName("gml_Object_dialog_control_Create_0")', patch)
        self.assertIn(
            "global.cur_day >= 1001 && !instance_exists(ag_bridge_controller)",
            patch,
        )
        self.assertIn(
            "global.cur_day >= 1001 && !instance_exists(ag_tablet_controller)",
            patch,
        )
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
        self.assertIn("global.ag_request_epoch += 1", controller_create)
        self.assertIn("ag_request_scope", controller_create)
        self.assertIn("ag_request_scope", controller)
        self.assertIn("ag_request_scope", controller_http)
        self.assertIn("current_time + 120000", controller_http)
        self.assertIn('ag_wait_box.input_text[0] = "..."', controller)
        self.assertIn("dialogue_wait", controller)
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
        self.assertIn("scorepop_obj", controller_http)
        self.assertIn("ag_scorepop_instance", controller_http)
        self.assertIn("global.jillwallet += global.cashcounter", controller_http)
        self.assertIn("instance_exists(new_day)", controller_http)
        self.assertIn("instance_create(x, y, new_day)", controller_http)
        self.assertIn('global.datestring = "O.S. DAY "', controller_http)
        self.assertIn('string_delete(ag_completed_day, 1, 4)', controller_http)
        self.assertNotIn('global.ag_story_day = real(ag_completed_day) + 1', controller_http)
        self.assertNotIn("global.money", controller_http)
        self.assertNotIn('"ini_close", "is_undefined"', patch)
        self.assertIn("ag_was_order_response = (ag_state == 7)", controller_http)
        self.assertIn("ag_ack_error_root", controller_http)
        self.assertIn("场景确认被拒绝（", controller_http)
        self.assertIn("else if (ag_was_order_response)", controller_http)
        self.assertIn("ag_error_code", controller_http)
        self.assertIn("json_decode(ag_result)", controller_http)
        self.assertIn("本轮调酒结果无法确认（", controller_http)
        self.assertIn("resetmixer_2()", controller_http)
        self.assertIn('"order_started"', controller)
        self.assertIn('"/v1/orders/resolve"', mixcontrol)
        self.assertIn("global.cur_day >= 1001", mixcontrol)
        self.assertIn("ag_request_scope", mixcontrol)
        self.assertIn("global.mod_aa", mixcontrol)
        self.assertIn("global.failed_a", mixcontrol)
        self.assertNotIn("claimed_result", mixcontrol)
        self.assertIn('ag_expected_status = "paired"', save_http)
        self.assertIn('ag_expected_status = "restored"', save_http)
        self.assertNotIn('ag_operation + "ed"', save_http)
        self.assertIn("jill_room", save_flow)
        self.assertIn("ag_preload_controller", save_flow)
        self.assertIn('global.datestring = "O.S. DAY "', save_flow)
        self.assertNotIn("instance_create(x, y, out_of_apartment)", save_flow)
        self.assertIn("ag_flow_state = 4", save_flow)
        self.assertNotIn("instance_create(room_width / 2, 165, save_home)", save_flow)
        self.assertNotIn("global.cur_data = \"save\"", save_flow)
        self.assertNotIn("global.block_click = 1", save_flow_create)
        self.assertIn('ag_operation = "restore"', load_slot)
        self.assertIn('Data.Code.ByName("gml_Script_load_slot_script")', patch)
        self.assertIn('Name = Data.Strings.MakeString("ag_save_controller")', patch)
        self.assertIn('Name = Data.Strings.MakeString("ag_save_flow_controller")', patch)
        self.assertIn("for (int slot = 1; slot <= 24; slot++)", patch)
        self.assertNotIn("data_icon.alarm[0] = 10", towork)
        self.assertNotIn("data_icon.chosen = 1", towork)
        self.assertNotIn("save_required_", controller_http)
        self.assertNotIn("ag_pair_complete", save_flow)
        self.assertIn("instance_create(x, y, out_of_apartment)", towork)
        self.assertIn("global.ag_story_day_advance_applied = 0", towork)
        self.assertIn("global.ag_prefetch_ready == 1", towork)
        self.assertIn("global.ag_prefetch_day == global.ag_story_day", towork)
        self.assertIn("var ag_story_ready", towork)
        self.assertIn("var ag_intro_blocking", towork)
        self.assertIn("work_click rejected", towork)
        self.assertIn("work_click accepted", towork)
        self.assertIn("with (popup_room) away = 1", towork)
        self.assertIn("global.cur_day >= 1001", towork)
        self.assertIn('"/v1/story/prepare"', preload_create)
        self.assertIn("preload request_sent", preload_create)
        self.assertIn("ag_preload_retry_at = 0", preload_create)
        self.assertIn("global.ag_request_epoch += 1", preload_create)
        self.assertIn("ag_preload_scope", preload_create)
        self.assertIn("ag_preload_scope", preload_step)
        self.assertIn("ag_wait_speaker", controller_create)
        self.assertIn('"/v1/scenes/jobs"', controller_create)
        self.assertIn("ag_scene_job_id", controller_create)
        self.assertIn('ag_wait_box.input_text[0] = "..."', controller)
        self.assertIn("dialogue_wait", controller)
        self.assertIn("dialogue_callback", controller_http)
        self.assertIn("dialogue_ready", controller_http)
        self.assertIn("dialogue_job_queued", controller_http)
        self.assertIn("dialogue_job_ready", controller_http)
        self.assertIn("speaker_hint", controller_http)
        self.assertIn("/v1/scenes/jobs/", controller)
        self.assertIn("ag_state == 8", controller)
        self.assertNotIn("instance_create(x, y, obj_textbox)", preload_step)
        self.assertIn("ag_open_shift_click_armed = 0", popup_room_create)
        self.assertIn("!mouse_check_button(mb_left)", popup_room_step)
        self.assertIn("mouse_check_button_pressed(mb_left)", popup_room_step)
        self.assertIn("else if (mouse_check_button(mb_left)", popup_room_step)
        self.assertIn("global.jillcomment", preload_step)
        self.assertIn("preload state=", preload_step)
        self.assertIn("preload retry mode=auto", preload_step)
        self.assertIn("5秒后自动重试", preload_step)
        self.assertIn("今日世界状态已准备完成", preload_http)
        self.assertIn('global.jillcomment = "O.S.：今日世界状态已准备完成。";', preload_http)
        self.assertNotIn('global.jillcomment = "JILL:', preload_http)
        self.assertIn("preload callback", preload_http)
        self.assertIn("preload ready", preload_http)
        self.assertIn("preload invalid_response", preload_http)
        self.assertIn("preload http_error", preload_http)
        self.assertIn("ag_preload_retry_at = current_time + 5000", preload_http)
        self.assertIn("shift_phase", preload_http)
        self.assertIn("global.ag_story_day", preload_http)
        self.assertIn('global.datestring = "O.S. DAY " + string(global.ag_story_day)', preload_step)
        self.assertIn("deadline = global.datestring", preload_step)
        self.assertIn("distraction = \"Glitch City 的日子仍在继续", preload_step)
        self.assertIn("unlocked = \"今日世界状态已准备完成", preload_step)
        self.assertIn("dismiss = \"点击鼠标关闭\"", preload_step)
        self.assertIn("other.ag_preload_state == 1", preload_step)
        self.assertNotIn("else if (ag_preload_state == 1)", preload_step)
        self.assertNotIn("if (room != jill_room)\n    instance_destroy();", preload_step)
        self.assertNotIn('global.datestring = "O.S. DAY 1"', show_room)
        self.assertIn("global.ag_story_day", show_room)
        self.assertIn("global.ag_prefetch_day", show_room)
        self.assertIn("global.ag_preload_notice_day", show_room)
        self.assertIn("with (ag_preload_controller) instance_destroy()", save_flow)
        self.assertIn("instance_create(x, y, ag_preload_controller)", save_flow)
        self.assertIn("global.ag_story_day = 1", variable_create)
        self.assertIn("global.ag_story_day_advance_applied = 0", variable_create)
        self.assertIn("global.ag_prefetch_day = 0", variable_create)
        self.assertIn("global.ag_preload_notice_day = 0", variable_create)
        self.assertIn("global.ag_request_epoch = 0", variable_create)
        self.assertIn("global.ag_story_day += 1", new_day)
        self.assertIn("global.cur_day >= 1001", new_day)
        self.assertIn("global.ag_story_day = ag_response_world_day", save_http)
        self.assertIn("OPEN SHIFT", show_room)
        self.assertIn("room_text", show_room)
        self.assertIn("点击鼠标关闭", show_room)
        self.assertNotIn("draw_rectangle", preload_draw)
        self.assertIn("global.shop_casitas = 1", show_room)
        self.assertIn("global.gotmeshop = 1", show_room)
        self.assertIn("global.cur_day >= 1001", show_room)
        self.assertIn("正在准备今天的营业", show_room)
        self.assertIn("global.cur_day >= 1001", aa_button_1)
        self.assertIn("global.cur_news = 52", aa_button_1)
        self.assertIn("global.hl53", tablet_create)
        self.assertIn("global.hl54", tablet_create)
        self.assertIn("global.hl55", tablet_create)
        self.assertIn("global.artcomment1", tablet_create)
        self.assertIn("本地服务暂时不可用", preload_http)
        self.assertNotIn("ag_aa_art1_draw.gml", patch)
        self.assertNotIn("InstallNewsFrame", patch)
        self.assertIn('Data.Code.ByName("gml_Object_aa_button_1_Step_0")', patch)
        self.assertIn('Data.Code.ByName("gml_Object_show_room_Create_0")', patch)
        self.assertIn('Data.Code.ByName("gml_Object_popup_room_Create_0")', patch)
        self.assertIn('Data.Code.ByName("gml_Object_popup_room_Step_0")', patch)
        self.assertIn('Data.Code.ByName("gml_Object_towork_button_Mouse_4")', patch)

    def test_recurring_open_shift_hooks_reject_day_one_only_gate(self) -> None:
        root = Path(__file__).resolve().parents[1] / "game-patch" / "gml"
        with tempfile.TemporaryDirectory() as temp_dir:
            copy = Path(temp_dir)
            for source in root.glob("*.gml"):
                text = source.read_text(encoding="utf-8")
                if source.name == "ag_bridge_mixcontrol_append.gml":
                    text = text.replace(
                        "global.cur_day >= 1001", "global.cur_day == 1001"
                    )
                (copy / source.name).write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(PatchContractError, "DAY 2\\+"):
                validate_patch_source_tree(copy)

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
