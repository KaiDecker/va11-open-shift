if (ag_state == 8 && ag_http_request == -1 && current_time >= ag_scene_job_poll_at)
{
    var ag_poll_headers;
    ag_poll_headers = ds_map_create();
    ds_map_add(ag_poll_headers, "Content-Type", "application/json");
    ini_open("open-shift-runtime.ini");
    ds_map_add(ag_poll_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
    ini_close();
    ag_http_request = http_request(ag_bridge_url + "/v1/scenes/jobs/" + ag_scene_job_id + "/result", "GET", ag_poll_headers, "");
    ds_map_destroy(ag_poll_headers);
    ag_scene_job_poll_count += 1;
    ag_scene_job_poll_at = current_time + 750;
    show_debug_message("[OPEN SHIFT] dialogue_job_poll job=" + ag_scene_job_id + " count=" + string(ag_scene_job_poll_count));
}

if (ag_state == 9)
{
    // The original jukebox closes itself after the native READY button calls
    // jukebox_advance(). Only then acknowledge the music scene to Python.
    if (global.jukebox_happens == 0 && !instance_exists(jukebox_bg) && !instance_exists(obj_textbox))
    {
        var ag_music_headers;
        var ag_music_body;
        ag_music_headers = ds_map_create();
        ds_map_add(ag_music_headers, "Content-Type", "application/json");
        ini_open("open-shift-runtime.ini");
        ds_map_add(ag_music_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
        ini_close();
        ag_music_body = ds_map_create();
        ag_request_sequence += 1;
        ag_request_id = "ack_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_request_sequence);
        ds_map_add(ag_music_body, "protocol_version", 1);
        ds_map_add(ag_music_body, "request_id", ag_request_id);
        ds_map_add(ag_music_body, "client_session_id", ag_session_id);
        ds_map_add(ag_music_body, "scene_id", ag_scene_id);
        ds_map_add(ag_music_body, "outcome", "continued_in_bar");
        ag_http_request = http_request(ag_bridge_url + "/v1/scenes/ack", "POST", ag_music_headers, json_encode(ag_music_body));
        ds_map_destroy(ag_music_body);
        ds_map_destroy(ag_music_headers);
        ag_music_gate_active = 0;
        ag_timeout_at = current_time + 3000;
        ag_state = 3;
        show_debug_message("[OPEN SHIFT] native_jukebox_complete scene=" + ag_scene_id);
    }
}

if (ag_state == 11 && ag_http_request == -1 && current_time >= ag_order_job_poll_at)
{
    var ag_order_poll_headers;
    ag_order_poll_headers = ds_map_create();
    ds_map_add(ag_order_poll_headers, "Content-Type", "application/json");
    ini_open("open-shift-runtime.ini");
    ds_map_add(ag_order_poll_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
    ini_close();
    ag_http_request = http_request(ag_bridge_url + "/v1/orders/jobs/" + ag_order_job_id + "/result", "GET", ag_order_poll_headers, "");
    ds_map_destroy(ag_order_poll_headers);
    ag_order_job_poll_count += 1;
    ag_order_job_poll_at = current_time + 750;
    show_debug_message("[OPEN SHIFT] order_job_poll job=" + ag_order_job_id + " count=" + string(ag_order_job_poll_count));
}

// Keep the provider placeholder inert while a scene or order job is pending.
// The vanilla textbox remains in the room, but confirmation cannot advance
// or dismiss it; the HTTP callback owns its lifetime.
// Contract marker: ag_wait_box.input_text[0] = "..." is represented by the
// native memory line below, not by direct textbox internals.
if (instance_exists(ag_wait_box) && ag_wait_box.ag_open_shift_wait == 1)
{
    global.block_click = 1;
}

if (ag_state == 10)
{
    // Use the original break_time room. Its break_changer creates save_home,
    // break_savereturn and break_savehome, so the four-portrait save page and
    // the optional save/load slots remain completely vanilla.
    if (room == break_time)
    {
        ag_break_room_entered = 1;
        global.block_click = 0;
        if (ag_break_wait_logged == 0)
        {
            ag_break_wait_logged = 1;
            show_debug_message("[OPEN SHIFT] native_break_room_wait");
        }
    }
    else if (ag_break_room_entered == 1 && room == bar && ag_http_request == -1 && !instance_exists(saveloadpage) && !instance_exists(save_home) && !instance_exists(break_savereturn) && !instance_exists(break_savehome) && !instance_exists(oob_bumper))
    {
        // The native break room owns the save page and its transition back to
        // the bar. Do not replay synthetic post-break dialogue here; resume
        // the bridge cursor directly and clear the vanilla mixer state first.
        ag_break_returned = 1;
        audio_stop_all();
        resetmixer_2();
        // Match the original break_return() contract before the persistent
        // bridge resumes its graph. The vanilla bar objects use these values
        // to start at the post-break customer, and an old textbox can survive
        // the room transition with an empty output buffer.
        global.cur_client = -2;
        global.cur_stage = 1;
        global.cur_data = "";
        global.cur_datapage = 1;
        global.mixhappens = 0;
        global.keeptext = 0;
        global.clickable = 1;
        global.output_text = "";
        if (instance_exists(obj_textbox))
        {
            with (obj_textbox) instance_destroy();
            show_debug_message("[OPEN SHIFT] native_break_room_cleanup textbox_destroyed=1");
        }
        global.block_click = 1;
        ag_line_index = ag_line_count;
        ag_line_active = 0;
        ag_timeout_at = current_time + 3000;
        ag_state = 2;
        show_debug_message("[OPEN SHIFT] native_break_room_return scene=" + ag_scene_id + " resume_index=end mixer_reset=1");
    }
}

if ((ag_state == 1 || ag_state == 3 || ag_state == 7 || ag_state == 8 || ag_state == 9 || ag_state == 10 || ag_state == 11) && current_time > ag_timeout_at)
{
    if (ag_state == 3)
    {
        ag_state = 4;
        ag_error_message = "O.S.：本地世界服务没有确认场景结果。";
    }
    else if (ag_state == 7 || ag_state == 11)
    {
        ag_state = 4;
        ag_http_request = -1;
        if (instance_exists(ag_wait_box))
        {
            with (ag_wait_box) instance_destroy();
            ag_wait_box = noone;
        }
        ag_error_message = "O.S.：本地世界服务没有返回调酒结果。";
    }
    else if (ag_state == 8)
    {
        ag_state = 4;
        ag_http_request = -1;
        if (instance_exists(ag_wait_box))
        {
            with (ag_wait_box) instance_destroy();
            ag_wait_box = noone;
        }
        ag_error_message = "O.S.：对白生成超时，请查看 timing.log。";
    }
    else if (ag_state == 9)
    {
        ag_state = 4;
        global.jukebox_happens = 0;
        global.block_click = 0;
        ag_error_message = "O.S.：原版点唱机在限定时间内没有完成，请查看 timing.log。";
    }
    else if (ag_state == 10)
    {
        ag_state = 4;
        global.block_click = 0;
        ag_error_message = "O.S.：中场存档页面在限定时间内没有关闭，请查看 timing.log。";
    }
    else
    {
        ag_state = 4;
        ag_error_message = "O.S.：本地世界服务没有响应。";
    }
}

if ((ag_state == 1 || ag_state == 7 || ag_state == 8 || ag_state == 11) && !instance_exists(obj_textbox))
{
    if (ag_state != 8)
        ag_wait_speaker = "";
    if (ag_state == 7 || ag_state == 11)
        ag_wait_speaker = ag_order_customer;
    ag_wait_started_at = current_time;
    // Route the placeholder through the same original textbox lifecycle as
    // real dialogue. It is still inert until the provider callback arrives.
    var ag_wait_line;
    ag_wait_line = "";
    if (ag_wait_speaker == "dana") { if (ag_portrait_speaker != "dana") { ag_wait_line += "[HIDEALL:][SHOW:185,sprite_dana]"; ag_portrait_speaker = "dana"; } ag_wait_line += "[XS:danaface,][XS:dantalk,1][C:15]Dana：[C:C]... [XS:dantalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "dorothy") { if (ag_portrait_speaker != "dorothy") { ag_wait_line += "[HIDEALL:][SHOW:185,sprite_doro]"; ag_portrait_speaker = "dorothy"; } ag_wait_line += "[XS:doroface,][XS:dorotalk,1][C:18]Dorothy：[C:C]... [XS:dorotalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "alma") { if (ag_portrait_speaker != "alma") { ag_wait_line += "[HIDEALL:][SHOW:185,sprite_alma]"; ag_portrait_speaker = "alma"; } ag_wait_line += "[XS:almaface,][XS:almatalk,1][C:14]Alma：[C:C]... [XS:almatalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "stella") { if (ag_portrait_speaker != "stella") { ag_wait_line += "[HIDEALL:][SHOW:185,sprite_stella]"; ag_portrait_speaker = "stella"; } ag_wait_line += "[XS:stelface,][XS:steltalk,1][C:16]Stella：[C:C]... [XS:steltalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "sei") { if (ag_portrait_speaker != "sei") { ag_wait_line += "[HIDEALL:][SHOW:185,sprite_sei]"; ag_portrait_speaker = "sei"; } ag_wait_line += "[XS:seiface,][XS:seitalk,1][C:17]Sei：[C:C]... [XS:seitalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "jill") ag_wait_line += "[XS:jilltalk,1][C:13]Jill：[C:C]... [STOPLIP:]";
    else ag_wait_line += "...";
    global.ag_memory_textbox_lines[0] = ag_wait_line;
    global.ag_memory_textbox_line_count = 1;
    global.ag_memory_textbox_active = 1;
    global.ag_memory_textbox_wait = 1;
    ag_wait_box = textbox_create_alt("", 0, 0);
    global.ag_memory_textbox_active = 0;
    global.ag_memory_textbox_wait = 0;
    global.output_text = "";
    show_debug_message("[OPEN SHIFT] dialogue_wait state=" + string(ag_state) + " speaker=" + ag_wait_speaker + " request=" + ag_request_id + " started_ms=" + string(ag_wait_started_at));
}

if (ag_state == 2 && !instance_exists(obj_textbox))
{
    if (ag_line_active)
    {
        reset_lips();
        ag_line_active = 0;
        ag_line_index += 1;
    }

    if (ag_line_index < ag_line_count)
    {
        var ag_current_speaker;
        var ag_current_expression;
        ag_current_speaker = ag_speaker[ag_line_index];
        ag_current_expression = ag_expression[ag_line_index];

        var ag_textbox;
        var ag_memory_line;
        var ag_name;
        var ag_face;
        var ag_talk;
        ag_memory_line = "";
        ag_name = ag_display_name[ag_line_index];
        if (ag_current_speaker == "dana") { ag_face = "danaface"; ag_talk = "dantalk"; }
        else if (ag_current_speaker == "dorothy") { ag_face = "doroface"; ag_talk = "dorotalk"; }
        else if (ag_current_speaker == "alma") { ag_face = "almaface"; ag_talk = "almatalk"; }
        else if (ag_current_speaker == "stella") { ag_face = "stelface"; ag_talk = "steltalk"; }
        else if (ag_current_speaker == "sei") { ag_face = "seiface"; ag_talk = "seitalk"; }
        else if (ag_current_speaker == "jill") { ag_face = ""; ag_talk = "jilltalk"; }
        else { ag_face = ""; ag_talk = ""; }
        if (ag_current_speaker != "" && ag_current_speaker != "jill")
        {
            // Keep the current customer's portrait across Jill's replies and
            // repeated lines. The original scripts only HIDEALL when the
            // speaker actually changes.
            if (ag_portrait_speaker != ag_current_speaker)
            {
            ag_memory_line += "[HIDEALL:][SHOW:185," + string(ag_portrait[ag_line_index]) + "]";
                ag_portrait_speaker = ag_current_speaker;
            }
            ag_memory_line += "[XS:" + ag_face + ",";
        }
        if (ag_current_speaker != "" && ag_current_speaker != "jill")
        {
            if (ag_current_expression == "happy")
            {
                if (ag_current_speaker == "dana") ag_memory_line += "closedsmile";
                else if (ag_current_speaker == "stella") ag_memory_line += "happy";
                else ag_memory_line += "smile";
            }
            else if (ag_current_expression == "worry")
            {
                if (ag_current_speaker == "dana") ag_memory_line += "worry";
                else if (ag_current_speaker == "stella") ag_memory_line += "concern";
                else ag_memory_line += "worried";
            }
            else if (ag_current_expression == "playful")
            {
                if (ag_current_speaker == "dana") ag_memory_line += "eee";
                else if (ag_current_speaker == "stella") ag_memory_line += "baka";
                else ag_memory_line += "smug";
            }
            ag_memory_line += "][XS:" + ag_talk + ",1]";
        }
        else if (ag_current_speaker == "jill")
        {
            // Jill's first line in a new scene should not inherit yesterday's
            // customer, while later Jill lines keep the current counterpart.
            if (ag_portrait_speaker == "")
            {
                ag_memory_line += "[HIDEALL:]";
                ag_portrait_speaker = "jill";
            }
            ag_memory_line += "[XS:jilltalk,1]";
        }
        if (ag_current_speaker != "")
            ag_memory_line += "[C:" + string(ag_name_color[ag_line_index]) + "]" + ag_name + "：[C:C]";
        // The vanilla renderer treats '#' as a line break. Provider text is
        // validated without command markers, so add safe breaks here instead
        // of letting a long Chinese sentence run past the native box.
        var ag_raw_text;
        var ag_wrapped_text;
        var ag_wrap_candidate;
        var ag_wrap_count;
        var ag_wrap_length;
        ag_raw_text = ag_text[ag_line_index];
        ag_wrapped_text = "";
        // The speaker name is part of the first rendered line in the native
        // textbox. Reserve its pixel width so `Dana：` plus Chinese text does
        // not overflow even when the dialogue body itself fits 380 pixels.
        ag_wrap_candidate = "";
        if (ag_current_speaker != "")
            ag_wrap_candidate = ag_name + "：";
        ag_wrap_count = 0;
        ag_wrap_length = string_length(ag_raw_text);
        draw_set_font(global.fnt_textbox);
        for (var ag_wrap_i = 1; ag_wrap_i <= ag_wrap_length; ag_wrap_i += 1)
        {
            var ag_wrap_char;
            ag_wrap_char = string_char_at(ag_raw_text, ag_wrap_i);
            if (string_width(ag_wrap_candidate + ag_wrap_char) > 380 && ag_wrap_count > 0)
            {
                ag_wrapped_text += "#";
                ag_wrap_candidate = "";
                ag_wrap_count = 0;
            }
            ag_wrapped_text += ag_wrap_char;
            ag_wrap_candidate += ag_wrap_char;
            ag_wrap_count += 1;
        }
        ag_memory_line += ag_wrapped_text;
        if (ag_current_speaker != "") ag_memory_line += "[XS:" + ag_talk + ",0]";
        ag_memory_line += "[STOPLIP:]";
        global.ag_memory_textbox_lines[0] = ag_memory_line;
        global.ag_memory_textbox_line_count = 1;
        global.ag_memory_textbox_active = 1;
        ag_textbox = textbox_create_alt("", 0, 1);
        global.ag_memory_textbox_active = 0;
        global.output_text = "";
        ag_line_active = 1;
    }
    else
    {
        if (string_copy(ag_scene_id, 1, 20) == "music_selection_day_")
        {
            // Hold the Python scene acknowledgement until the player has
            // completed the original playlist UI.
            global.jukebox_happens = 1;
            global.block_click = 0;
            ag_music_gate_active = 1;
            ag_timeout_at = current_time + 900000;
            ag_state = 9;
            show_debug_message("[OPEN SHIFT] native_jukebox_pending scene=" + ag_scene_id);
        }
        else if (string_copy(ag_scene_id, 1, 10) == "break_day_" && ag_break_returned == 0)
        {
            // Leave the bridge scene unacknowledged while the native break
            // room owns the save UI.  The persistent controller resumes the
            // acknowledgement after the player closes that UI.
            global.block_click = 0;
            global.cur_data = "";
            global.cur_datapage = 1;
            audio_stop_all();
            ag_timeout_at = current_time + 900000;
            ag_break_wait_logged = 0;
            ag_break_room_entered = 0;
            ag_break_returned = 0;
            ag_state = 10;
            room_goto(break_time);
            show_debug_message("[OPEN SHIFT] native_break_pending scene=" + ag_scene_id);
        }
        else
        {
        var ag_headers;
        var ag_body;
        ag_headers = ds_map_create();
        ds_map_add(ag_headers, "Content-Type", "application/json");
        ini_open("open-shift-runtime.ini");
        ds_map_add(ag_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
        ini_close();
        ag_body = ds_map_create();
        ds_map_add(ag_body, "protocol_version", 1);
        ds_map_add(ag_body, "request_id", "ack_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_request_sequence));
        ds_map_add(ag_body, "client_session_id", ag_session_id);
        ds_map_add(ag_body, "scene_id", ag_scene_id);
        if (ag_order_pending)
            ds_map_add(ag_body, "outcome", "order_started");
        else
            ds_map_add(ag_body, "outcome", "continued_in_bar");
        if (string_copy(ag_scene_id, 1, 10) == "break_day_")
            show_debug_message("[OPEN SHIFT] native_break_room_ack scene=" + ag_scene_id);
        ag_http_request = http_request(ag_bridge_url + "/v1/scenes/ack", "POST", ag_headers, json_encode(ag_body));
        ds_map_destroy(ag_body);
        ds_map_destroy(ag_headers);
        ag_timeout_at = current_time + 3000;
        ag_state = 3;
        }
    }
}

if (ag_state == 4 && !instance_exists(obj_textbox))
{
    var ag_error_box;
    var ag_error_wrapped;
    var ag_error_buffer;
    ag_error_box = noone;
    // Error text is assembled from fixed client strings and a validated
    // service error code, so it is safe to pass through the native loader.
    ag_error_wrapped = ag_error_message;
    ag_error_buffer = "";
    global.ag_memory_textbox_lines[0] = ag_error_wrapped;
    global.ag_memory_textbox_line_count = 1;
    global.ag_memory_textbox_active = 1;
    ag_error_box = textbox_create_alt("", 0, 1);
    global.ag_memory_textbox_active = 0;
    global.output_text = "";
    ag_state = 5;
}

if (ag_state == 5 && !instance_exists(obj_textbox))
{
    global.block_click = 0;
    instance_destroy();
}
