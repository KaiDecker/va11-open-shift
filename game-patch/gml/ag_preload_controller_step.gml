if (ag_preload_state == 1 && current_time > ag_preload_timeout_at)
{
    // Do not let the original work button enter a half-initialized bar.
    // The player must retry after the daily graph is actually ready.
    ag_preload_state = 3;
    ag_preload_error = "O.S.：剧情准备超时，请点击重试。";
    global.ag_prefetch_ready = 0;
    global.ag_prefetch_failed = 1;
    global.ag_prefetch_day = 0;
    global.jillcomment = "O.S.：今日营业还没准备好，先等本地服务。";
    ag_preload_debug_last_event = "timeout";
    ag_preload_finished_at = current_time;
    ag_preload_retry_at = current_time + 5000;
    show_debug_message("[OPEN SHIFT] preload timeout day=" + string(global.ag_story_day) + " elapsed_ms=" + string(ag_preload_finished_at - ag_preload_started_at));
}

if (ag_preload_debug_last_state != ag_preload_state)
{
    ag_preload_debug_last_state = ag_preload_state;
    show_debug_message("[OPEN SHIFT] preload state=" + string(ag_preload_state) + " event=" + ag_preload_debug_last_event + " day=" + string(global.ag_story_day));
}

if (room == jill_room && global.ag_open_shift_intro_pending == 1 && !instance_exists(popup_room))
{
    global.ag_open_shift_intro_pending = 0;
    global.ag_open_shift_intro_seen = 1;
}

if (ag_preload_state == 2)
{
    global.ag_prefetch_ready = 1;
    global.ag_prefetch_failed = 0;
    global.ag_prefetch_day = global.ag_story_day;
    // room_text can be created after the HTTP callback on the legacy room
    // transition. Keep the visible apartment banner synchronized with the
    // authoritative world day on every frame until the player leaves.
    global.datestring = "O.S. DAY " + string(global.ag_story_day);
    if (room == jill_room && instance_exists(room_text))
    {
        with (room_text)
        {
            deadline = global.datestring;
            distraction = "Glitch City 的日子仍在继续。#熟悉的客人也有了新的生活。";
            unlocked = "今日世界状态已准备完成。点击‘去酒吧上班’后实时生成对白。";
            dismiss = "点击鼠标关闭";
        }
    }
    // If the player dismissed the original room popup while the graph was
    // loading, show one fresh completion notice in the same room. The day
    // marker prevents it from reappearing every frame after dismissal.
    if (room == jill_room && global.ag_preload_notice_day != global.ag_story_day)
    {
        if (!instance_exists(popup_room))
            instance_create(x, y, popup_room);
        global.ag_preload_notice_day = global.ag_story_day;
    }
}

// room_text is created after the room controller on some legacy transitions.
// Keep the original popup's four text fields populated even when that order
// changes, including the first-entry introduction and the loading state.
if (room == jill_room && instance_exists(room_text))
{
    with (room_text)
    {
        if (global.ag_open_shift_intro_pending == 1 || global.ag_open_shift_intro_seen == 0)
        {
            deadline = "OPEN SHIFT";
            distraction = "Glitch City 的日子仍在继续。#熟悉的客人也有了新的生活。";
            unlocked = "先看看房间里的新闻，准备完成后再去酒吧上班。";
        }
        else if (other.ag_preload_state == 1)
        {
            deadline = global.datestring;
            distraction = "今天的营业已进入实时模式。";
            unlocked = "点击‘去酒吧上班’后，角色对白会按场景生成。";
        }
        else if (global.ag_prefetch_failed == 1)
        {
            deadline = global.datestring;
            distraction = "本地世界服务暂时不可用。";
            if (other.ag_preload_retry_at > 0)
                unlocked = "今日剧情还没准备好，5秒后自动重试；现在不能去酒吧上班。";
            else
                unlocked = "今日剧情还没准备好，请点击‘重试’；现在不能去酒吧上班。";
        }
        dismiss = "点击鼠标关闭";
    }
}

var ag_preload_retry_click = ag_preload_state == 3 && mouse_check_button_pressed(mb_left) && mouse_x >= 235 && mouse_x <= 440 && mouse_y >= 185 && mouse_y <= 250;
var ag_preload_retry_auto = ag_preload_state == 3 && ag_preload_retry_at > 0 && current_time >= ag_preload_retry_at;
if (ag_preload_retry_click || ag_preload_retry_auto)
{
    if (ag_preload_retry_auto)
        show_debug_message("[OPEN SHIFT] preload retry mode=auto day=" + string(global.ag_story_day));
    else
        show_debug_message("[OPEN SHIFT] preload retry mode=manual day=" + string(global.ag_story_day));
    ini_open("open-shift-runtime.ini");
    ag_preload_port = ini_read_real("bridge", "port", 8711);
    ag_preload_token = ini_read_string("bridge", "token", "");
    ag_preload_session = ini_read_string("bridge", "session_id", "");
    ini_close();
    if (ag_preload_port >= 1 && ag_preload_port <= 65535 && string_length(ag_preload_token) >= 16 && string_length(ag_preload_session) >= 16)
    {
        var ag_retry_headers;
        var ag_retry_body;
        ag_preload_attempt += 1;
        ag_retry_headers = ds_map_create();
        ds_map_add(ag_retry_headers, "Content-Type", "application/json");
        ds_map_add(ag_retry_headers, "X-Open-Shift-Token", ag_preload_token);
        ag_retry_body = ds_map_create();
        ag_preload_request_id = "prepare_" + ag_preload_session + "_" + ag_preload_scope + "_" + string(ag_preload_attempt);
        ds_map_add(ag_retry_body, "protocol_version", 1);
        ds_map_add(ag_retry_body, "request_id", ag_preload_request_id);
        ds_map_add(ag_retry_body, "client_session_id", ag_preload_session);
        ag_preload_http_request = http_request("http://127.0.0.1:" + string(ag_preload_port) + "/v1/story/prepare", "POST", ag_retry_headers, json_encode(ag_retry_body));
        ds_map_destroy(ag_retry_body);
        ds_map_destroy(ag_retry_headers);
        ag_preload_token = "";
        ag_preload_timeout_at = current_time + 120000;
        ag_preload_retry_at = 0;
        ag_preload_started_at = current_time;
        ag_preload_finished_at = 0;
        ag_preload_error = "";
        ag_preload_state = 1;
        ag_preload_debug_last_event = "request_sent";
        show_debug_message("[OPEN SHIFT] preload request_sent day=" + string(global.ag_story_day) + " attempt=" + string(ag_preload_attempt) + " request=" + ag_preload_request_id);
        global.jillcomment = "O.S.：今天的对白会在酒吧实时生成……";
        global.ag_prefetch_failed = 0;
        global.ag_prefetch_day = 0;
        global.ag_preload_notice_day = 0;
    }
}

// Keep the controller alive while Jill views tablet/apps or other apartment
// overlays. The HTTP callback must still be delivered to this instance; the
// next return to Jill's room will recreate it only when the room controller
// explicitly starts a new authoritative preparation request.
