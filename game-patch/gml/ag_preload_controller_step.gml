if (ag_preload_state == 1 && current_time > ag_preload_timeout_at)
{
    ag_preload_state = 2;
    ag_preload_error = "O.S.：剧情准备超时，已切换本地剧情。";
    global.jillcomment = "JILL: 本地服务响应太慢，先按本地剧情继续。";
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
            unlocked = "今日营业已准备完成。点击‘去酒吧上班’开始。";
            dismiss = "点击鼠标关闭";
        }
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
            distraction = "今天的营业正在准备中。";
            unlocked = "本地世界服务正在生成今日剧情，请稍候。";
        }
        else if (global.ag_prefetch_failed == 1)
        {
            deadline = global.datestring;
            distraction = "本地世界服务暂时不可用。";
            unlocked = "已切换本地剧情，仍可去酒吧上班。";
        }
        dismiss = "点击鼠标关闭";
    }
}

if (ag_preload_state == 3 && mouse_check_button_pressed(mb_left) && mouse_x >= 235 && mouse_x <= 440 && mouse_y >= 185 && mouse_y <= 250)
{
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
        ag_preload_request_id = "prepare_" + ag_preload_session + "_" + string(ag_preload_attempt);
        ds_map_add(ag_retry_body, "protocol_version", 1);
        ds_map_add(ag_retry_body, "request_id", ag_preload_request_id);
        ds_map_add(ag_retry_body, "client_session_id", ag_preload_session);
        ag_preload_http_request = http_request("http://127.0.0.1:" + string(ag_preload_port) + "/v1/story/prepare", "POST", ag_retry_headers, json_encode(ag_retry_body));
        ds_map_destroy(ag_retry_body);
        ds_map_destroy(ag_retry_headers);
        ag_preload_token = "";
        ag_preload_timeout_at = current_time + 120000;
        ag_preload_error = "";
        ag_preload_state = 1;
        global.jillcomment = "JILL: 正在准备今天的营业……";
        global.ag_prefetch_failed = 0;
    }
}

if (room != jill_room)
    instance_destroy();
