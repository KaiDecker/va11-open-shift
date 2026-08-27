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

if (ag_state == 12)
{
    // After break_time the original game reopens the existing playlist. READY
    // closes jukebox_bg and restarts music_obj; only then may customer 3 load.
    if (ag_music_resume_pending == 1 && global.jukebox_happens == 0 && !instance_exists(jukebox_bg) && !instance_exists(obj_textbox))
    {
        var ag_resume_headers;
        var ag_resume_body;
        ag_resume_headers = ds_map_create();
        ds_map_add(ag_resume_headers, "Content-Type", "application/json");
        ini_open("open-shift-runtime.ini");
        ds_map_add(ag_resume_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
        ini_close();
        ag_resume_body = ds_map_create();
        ag_request_sequence += 1;
        ag_request_id = "open_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_request_sequence);
        ds_map_add(ag_resume_body, "protocol_version", 1);
        ds_map_add(ag_resume_body, "request_id", ag_request_id);
        ds_map_add(ag_resume_body, "client_session_id", ag_session_id);
        ag_http_request = http_request(ag_bridge_url + "/v1/scenes/jobs", "POST", ag_resume_headers, json_encode(ag_resume_body));
        ds_map_destroy(ag_resume_body);
        ds_map_destroy(ag_resume_headers);
        ag_music_resume_pending = 0;
        ag_music_gate_active = 0;
        ag_state = 1;
        global.block_click = 1;
        ag_timeout_at = current_time + 120000;
        show_debug_message("[OPEN SHIFT] native_jukebox_resume_complete playlist=reused next_scene=bridge http_id=" + string(ag_http_request));
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

// Client-side room/break diagnostics are fire-and-forget. Keep their handle
// separate from ag_http_request so a diagnostic callback cannot advance or
// block the gameplay state machine. The event is sent once at the end of this
// Step after the native transition fields have been captured.
var ag_diag_should_emit;
var ag_diag_state_name;
var ag_diag_previous_room;
var ag_diag_error_reason;
ag_diag_should_emit = 0;
ag_diag_state_name = "";
ag_diag_previous_room = ag_last_room;
ag_diag_error_reason = "";

// Persistent controller diagnostics: log each room transition once with the
// bridge and vanilla cursor state. This makes a failed native break hand-off
// distinguishable from a missing HTTP callback in the next acceptance run.
if (ag_last_room != room)
{
    show_debug_message("[OPEN SHIFT] room_change room=" + string(room) + " previous=" + string(ag_last_room) + " state=" + string(ag_state) + " http_id=" + string(ag_http_request) + " cur_client=" + string(global.cur_client) + " cur_stage=" + string(global.cur_stage));
    ag_diag_should_emit = 1;
    ag_diag_state_name = "room_change";
    ag_diag_error_reason = "room_change";
    ag_last_room = room;
}

if (ag_diag_should_emit == 1)
{
    ag_diag_request_sequence += 1;
    var ag_diag_headers;
    var ag_diag_body;
    var ag_diag_request_id;
    ag_diag_headers = ds_map_create();
    ds_map_add(ag_diag_headers, "Content-Type", "application/json");
    ini_open("open-shift-runtime.ini");
    ds_map_add(ag_diag_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
    ini_close();
    ag_diag_request_id = "diag_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_diag_request_sequence);
    ag_diag_body = ds_map_create();
    ds_map_add(ag_diag_body, "request_id", ag_diag_request_id);
    ds_map_add(ag_diag_body, "phase", "client");
    ds_map_add(ag_diag_body, "state", ag_diag_state_name);
    ds_map_add(ag_diag_body, "room_id", room);
    ds_map_add(ag_diag_body, "previous_room_id", ag_diag_previous_room);
    ds_map_add(ag_diag_body, "bridge_state", ag_state);
    ds_map_add(ag_diag_body, "active_http_id", ag_http_request);
    ds_map_add(ag_diag_body, "cur_client", global.cur_client);
    ds_map_add(ag_diag_body, "cur_stage", global.cur_stage);
    ds_map_add(ag_diag_body, "error_reason", ag_diag_error_reason);
    ag_diag_http_request = http_request(ag_bridge_url + "/v1/diagnostics/client-event", "POST", ag_diag_headers, json_encode(ag_diag_body));
    // The diagnostics callback is intentionally ignored by the gameplay HTTP
    // event. Release the slot immediately so a later transition can report.
    ag_diag_http_request = -1;
    ds_map_destroy(ag_diag_body);
    ds_map_destroy(ag_diag_headers);
    ag_diag_should_emit = 0;
}

if (ag_state == 10)
{
    // Use the original break_time room. Its break_changer creates save_home,
    // break_savereturn and break_savehome, so the four-portrait save page and
    // the optional save/load slots remain completely vanilla.
    if (room == break_time)
    {
        ag_break_room_entered = 1;
        // break_changer already called the vanilla break_return() while the
        // room was created. Never overwrite cur_client/cur_stage here: the
        // original save UI owns those values and will restore them on return.
        global.block_click = 0;
        if (ag_break_wait_logged == 0)
        {
            ag_break_wait_logged = 1;
            show_debug_message("[OPEN SHIFT] native_break_room_enter room=break_time state=" + string(ag_state) + " http_id=" + string(ag_http_request) + " cur_client=" + string(global.cur_client) + " cur_stage=" + string(global.cur_stage));
            ag_diag_should_emit = 1;
            ag_diag_state_name = "native_break_room_enter";
            ag_diag_error_reason = "native_break_room_enter";
        }
    }
    else if (ag_break_room_entered == 1 && room == bar)
    {
        // The native break room owns the save page and its transition back to
        // the bar. Do not replay synthetic post-break dialogue here. The
        // vanilla break_changer/break_return chain ran on room creation. The
        ag_break_returned = 1;
        // Keep the vanilla save lifecycle, then reopen the native jukebox
        // using the existing global.playlist before requesting customer 3.
        global.jukebox_happens = 1;
        ag_music_resume_pending = 1;
        ag_music_gate_active = 1;
        ag_state = 12;
        global.block_click = 0;
        ag_timeout_at = current_time + 900000;
        ag_break_room_entered = 0;
        ag_break_returned = 0;
        ag_break_wait_logged = 0;
        show_debug_message("[OPEN SHIFT] native_break_room_return room=bar bridge_state=12 native_jukebox_resume_pending=1 playlist=reused cur_client=" + string(global.cur_client) + " cur_stage=" + string(global.cur_stage));
        ag_diag_should_emit = 1;
        ag_diag_state_name = "native_break_room_return";
        ag_diag_error_reason = "native_jukebox_resume_pending";
    }
}

if (ag_diag_should_emit == 1)
{
    ag_diag_request_sequence += 1;
    var ag_break_diag_headers;
    var ag_break_diag_body;
    var ag_break_diag_request_id;
    ag_break_diag_headers = ds_map_create();
    ds_map_add(ag_break_diag_headers, "Content-Type", "application/json");
    ini_open("open-shift-runtime.ini");
    ds_map_add(ag_break_diag_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
    ini_close();
    ag_break_diag_request_id = "diag_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_diag_request_sequence);
    ag_break_diag_body = ds_map_create();
    ds_map_add(ag_break_diag_body, "request_id", ag_break_diag_request_id);
    ds_map_add(ag_break_diag_body, "phase", "client");
    ds_map_add(ag_break_diag_body, "state", ag_diag_state_name);
    ds_map_add(ag_break_diag_body, "room_id", room);
    ds_map_add(ag_break_diag_body, "previous_room_id", ag_diag_previous_room);
    ds_map_add(ag_break_diag_body, "bridge_state", ag_state);
    ds_map_add(ag_break_diag_body, "active_http_id", ag_http_request);
    ds_map_add(ag_break_diag_body, "cur_client", global.cur_client);
    ds_map_add(ag_break_diag_body, "cur_stage", global.cur_stage);
    ds_map_add(ag_break_diag_body, "error_reason", ag_diag_error_reason);
    ag_diag_http_request = http_request(ag_bridge_url + "/v1/diagnostics/client-event", "POST", ag_break_diag_headers, json_encode(ag_break_diag_body));
    ag_diag_http_request = -1;
    ds_map_destroy(ag_break_diag_body);
    ds_map_destroy(ag_break_diag_headers);
    ag_diag_should_emit = 0;
}

if ((ag_state == 1 || ag_state == 3 || ag_state == 7 || ag_state == 8 || ag_state == 9 || ag_state == 10 || ag_state == 11 || ag_state == 12) && current_time > ag_timeout_at)
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
    else if (ag_state == 12)
    {
        ag_state = 4;
        ag_music_resume_pending = 0;
        global.jukebox_happens = 0;
        global.block_click = 0;
        ag_error_message = "O.S.：中场后的原版点唱机在限定时间内没有完成，请查看 timing.log。";
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
    // Every textbox owns its own SHOW/cutin state. Re-emit the customer's
    // portrait for every placeholder, even when the scalar speaker cache is
    // unchanged, because the preceding native textbox may have hidden it.
    // hideall() only sets the vanilla global hide flags; the sprite object
    // fades out and is destroyed on a later Step. Reset the active customer's
    // flag before every placeholder SHOW so an old textbox cannot make the
    // customer disappear while the provider request is pending.
    if (ag_wait_speaker == "dana") { if (ag_portrait_speaker != "dana") { ag_wait_line += "[HIDEALL:]"; ag_portrait_speaker = "dana"; } global.danahide = 0; ag_wait_line += "[SHOW:185,sprite_dana]"; ag_wait_line += "[XS:danaface,][XS:dantalk,1][C:15]Dana：[C:C]... [XS:dantalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "dorothy") { if (ag_portrait_speaker != "dorothy") { ag_wait_line += "[HIDEALL:]"; ag_portrait_speaker = "dorothy"; } global.dorohide = 0; ag_wait_line += "[SHOW:185,sprite_doro]"; ag_wait_line += "[XS:doroface,][XS:dorotalk,1][C:18]Dorothy：[C:C]... [XS:dorotalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "alma") { if (ag_portrait_speaker != "alma") { ag_wait_line += "[HIDEALL:]"; ag_portrait_speaker = "alma"; } global.almahide = 0; ag_wait_line += "[SHOW:185,sprite_alma]"; ag_wait_line += "[XS:almaface,][XS:almatalk,1][C:14]Alma：[C:C]... [XS:almatalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "stella") { if (ag_portrait_speaker != "stella") { ag_wait_line += "[HIDEALL:]"; ag_portrait_speaker = "stella"; } global.stelhide = 0; ag_wait_line += "[SHOW:185,sprite_stella]"; ag_wait_line += "[XS:stelface,][XS:steltalk,1][C:16]Stella：[C:C]... [XS:steltalk,0][STOPLIP:]"; }
    else if (ag_wait_speaker == "sei") { if (ag_portrait_speaker != "sei") { ag_wait_line += "[HIDEALL:]"; ag_portrait_speaker = "sei"; } global.seihide = 0; ag_wait_line += "[SHOW:185,sprite_sei]"; ag_wait_line += "[XS:seiface,][XS:seitalk,1][C:17]Sei：[C:C]... [XS:seitalk,0][STOPLIP:]"; }
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
            // A native waiting textbox may execute HIDEALL before the provider
            // response replaces it. Always issue SHOW for a speaking customer
            // so the real portrait state is restored instead of trusting the
            // bridge's scalar cache.
            if (ag_portrait_speaker != ag_current_speaker)
            {
                ag_memory_line += "[HIDEALL:]";
                ag_portrait_speaker = ag_current_speaker;
            }
            // SHOWSPRITE skips an already existing sprite. The vanilla
            // sprite Step also respects the global hide flag, so clear the
            // flag explicitly before SHOW on the first real response.
            if (ag_current_speaker == "dana") global.danahide = 0;
            else if (ag_current_speaker == "dorothy") global.dorohide = 0;
            else if (ag_current_speaker == "alma") global.almahide = 0;
            else if (ag_current_speaker == "stella") global.stelhide = 0;
            else if (ag_current_speaker == "sei") global.seihide = 0;
            ag_memory_line += "[SHOW:185," + string(ag_portrait[ag_line_index]) + "]";
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
            // A new textbox must restore the counterpart cut-in before Jill's
            // reply. The native textbox owns SHOW state, so retaining only the
            // scalar speaker is insufficient after the waiting textbox closes.
            if (ag_portrait_speaker == "dana")
            {
                global.danahide = 0;
                ag_memory_line += "[SHOW:185,sprite_dana]";
            }
            else if (ag_portrait_speaker == "dorothy")
            {
                global.dorohide = 0;
                ag_memory_line += "[SHOW:185,sprite_doro]";
            }
            else if (ag_portrait_speaker == "alma")
            {
                global.almahide = 0;
                ag_memory_line += "[SHOW:185,sprite_alma]";
            }
            else if (ag_portrait_speaker == "stella")
            {
                global.stelhide = 0;
                ag_memory_line += "[SHOW:185,sprite_stella]";
            }
            else if (ag_portrait_speaker == "sei")
            {
                global.seihide = 0;
                ag_memory_line += "[SHOW:185,sprite_sei]";
            }
            else if (ag_portrait_speaker == "")
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
            // Commit progression before entering break_time. The native room
            // then performs break_return() and creates the original save UI.
            // This keeps the server state and paired vanilla save in order.
            ag_request_sequence += 1;
            ag_request_id = "ack_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_request_sequence);
            var ag_break_headers;
            var ag_break_body;
            ag_break_headers = ds_map_create();
            ds_map_add(ag_break_headers, "Content-Type", "application/json");
            ini_open("open-shift-runtime.ini");
            ds_map_add(ag_break_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
            ini_close();
            ag_break_body = ds_map_create();
            ds_map_add(ag_break_body, "protocol_version", 1);
            ds_map_add(ag_break_body, "request_id", ag_request_id);
            ds_map_add(ag_break_body, "client_session_id", ag_session_id);
            ds_map_add(ag_break_body, "scene_id", ag_scene_id);
            ds_map_add(ag_break_body, "outcome", "continued_in_bar");
            ag_http_request = http_request(ag_bridge_url + "/v1/scenes/ack", "POST", ag_break_headers, json_encode(ag_break_body));
            ds_map_destroy(ag_break_body);
            ds_map_destroy(ag_break_headers);
            ag_break_enter_after_ack = 1;
            ag_break_wait_logged = 0;
            ag_break_room_entered = 0;
            ag_break_returned = 0;
            ag_timeout_at = current_time + 3000;
            ag_state = 3;
            show_debug_message("[OPEN SHIFT] native_break_ack_pending scene=" + ag_scene_id + " state=3 http_id=" + string(ag_http_request) + " cur_client=" + string(global.cur_client) + " cur_stage=" + string(global.cur_stage));
            ag_diag_should_emit = 1;
            ag_diag_state_name = "native_break_ack_pending";
            ag_diag_previous_room = ag_last_room;
            ag_diag_error_reason = "native_break_ack_pending";
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
