if (ds_map_find_value(async_load, "id") == ag_preload_http_request)
{
    var ag_preload_status;
    var ag_preload_http_status;
    var ag_preload_result;
    var ag_preload_root;
    ag_preload_status = ds_map_find_value(async_load, "status");
    ag_preload_http_status = ds_map_find_value(async_load, "http_status");
    ag_preload_result = ds_map_find_value(async_load, "result");
    self.ag_preload_http_status = ag_preload_http_status;
    show_debug_message("[OPEN SHIFT] preload callback http_status=" + string(ag_preload_http_status) + " transport_status=" + string(ag_preload_status) + " request=" + ag_preload_request_id);
    if (ag_preload_status == 0 && ag_preload_http_status == 200 && string_length(string(ag_preload_result)) > 0)
    {
        ag_preload_root = json_decode(ag_preload_result);
        if (ds_exists(ag_preload_root, ds_type_map) && ds_map_size(ag_preload_root) == 7 && ds_map_exists(ag_preload_root, "protocol_version") && ds_map_exists(ag_preload_root, "request_id") && ds_map_exists(ag_preload_root, "world_day") && ds_map_exists(ag_preload_root, "status") && ds_map_exists(ag_preload_root, "opening_seen") && ds_map_exists(ag_preload_root, "shift_phase") && ds_map_exists(ag_preload_root, "last_completed_story_day") && ds_map_find_value(ag_preload_root, "protocol_version") == 1 && ds_map_find_value(ag_preload_root, "request_id") == ag_preload_request_id && ds_map_find_value(ag_preload_root, "world_day") >= 1 && ds_map_find_value(ag_preload_root, "status") == "ready")
        {
            ag_preload_state = 2;
            ag_preload_retry_at = 0;
            ag_preload_finished_at = current_time;
            ag_preload_retry_at = current_time + 5000;
            ag_preload_debug_last_event = "ready";
            show_debug_message("[OPEN SHIFT] preload ready day=" + string(ds_map_find_value(ag_preload_root, "world_day")) + " elapsed_ms=" + string(ag_preload_finished_at - ag_preload_started_at));
            global.ag_story_day = ds_map_find_value(ag_preload_root, "world_day");
            global.datestring = "O.S. DAY " + string(global.ag_story_day);
            global.jillcomment = "O.S.：今日世界状态已准备完成。";
            if (room == jill_room && global.ag_preload_notice_day != ds_map_find_value(ag_preload_root, "world_day"))
            {
                if (!instance_exists(popup_room))
                    instance_create(x, y, popup_room);
                global.ag_preload_notice_day = ds_map_find_value(ag_preload_root, "world_day");
            }
            if (instance_exists(room_text))
            {
                with (room_text)
                {
                    deadline = global.datestring;
                    unlocked = "今日世界状态已准备完成。对白会在酒吧实时生成。";
                }
            }
        }
        else
        {
            ag_preload_state = 3;
            ag_preload_finished_at = current_time;
            ag_preload_debug_last_event = "invalid_response";
            show_debug_message("[OPEN SHIFT] preload invalid_response day=" + string(global.ag_story_day));
            ag_preload_error = "O.S.：本地服务返回了无效响应，请点击重试。";
            global.ag_prefetch_ready = 0;
            global.ag_prefetch_failed = 1;
            global.ag_prefetch_day = 0;
            global.jillcomment = "O.S.：今日营业还没准备好，先重试一次。";
            if (instance_exists(room_text))
            {
                with (room_text)
                    unlocked = "今日剧情还没准备好，请点击‘重试’；现在不能去酒吧上班。";
            }
        }
        if (ds_exists(ag_preload_root, ds_type_map)) ds_map_destroy(ag_preload_root);
    }
    else
    {
        ag_preload_state = 3;
        ag_preload_finished_at = current_time;
        ag_preload_debug_last_event = "http_error";
        show_debug_message("[OPEN SHIFT] preload http_error status=" + string(ag_preload_http_status) + " day=" + string(global.ag_story_day));
        global.ag_prefetch_ready = 0;
        global.ag_prefetch_failed = 1;
        global.ag_prefetch_day = 0;
        if (ag_preload_http_status == 503)
            ag_preload_error = "O.S.：本地服务暂时不可用，请点击重试。";
        else if (ag_preload_http_status == 429)
        {
            ag_preload_error = "O.S.：API额度不足，请点击重试。";
            ag_preload_retry_at = 0;
        }
        else
            ag_preload_error = "O.S.：本地世界服务未响应，请点击重试。";
        if (ag_preload_http_status == 503 || ag_preload_http_status <= 0)
            ag_preload_retry_at = current_time + 5000;
        global.jillcomment = "O.S.：今日营业还没准备好，先重试一次。";
        if (instance_exists(room_text))
        {
            with (room_text)
                unlocked = "今日剧情还没准备好，请点击‘重试’；现在不能去酒吧上班。";
        }
    }
}
