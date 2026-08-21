if (ds_map_find_value(async_load, "id") == ag_preload_http_request)
{
    var ag_preload_status;
    var ag_preload_http_status;
    var ag_preload_result;
    var ag_preload_root;
    ag_preload_status = ds_map_find_value(async_load, "status");
    ag_preload_http_status = ds_map_find_value(async_load, "http_status");
    ag_preload_result = ds_map_find_value(async_load, "result");
    if (ag_preload_status == 0 && ag_preload_http_status == 200 && string_length(string(ag_preload_result)) > 0)
    {
        ag_preload_root = json_decode(ag_preload_result);
        if (ds_exists(ag_preload_root, ds_type_map) && ds_map_size(ag_preload_root) == 7 && ds_map_exists(ag_preload_root, "protocol_version") && ds_map_exists(ag_preload_root, "request_id") && ds_map_exists(ag_preload_root, "world_day") && ds_map_exists(ag_preload_root, "status") && ds_map_exists(ag_preload_root, "opening_seen") && ds_map_exists(ag_preload_root, "shift_phase") && ds_map_exists(ag_preload_root, "last_completed_story_day") && ds_map_find_value(ag_preload_root, "protocol_version") == 1 && ds_map_find_value(ag_preload_root, "request_id") == ag_preload_request_id && ds_map_find_value(ag_preload_root, "world_day") >= 1 && ds_map_find_value(ag_preload_root, "status") == "ready")
        {
            ag_preload_state = 2;
            global.ag_story_day = ds_map_find_value(ag_preload_root, "world_day");
            global.datestring = "O.S. DAY " + string(global.ag_story_day);
            global.jillcomment = "JILL: 今日营业已准备完成。";
            if (instance_exists(room_text))
            {
                with (room_text)
                {
                    deadline = global.datestring;
                    unlocked = "今日营业已准备完成。";
                }
            }
        }
        else
        {
            ag_preload_state = 2;
            ag_preload_error = "O.S.：本地服务返回了无效响应，已切换本地剧情。";
            global.jillcomment = "JILL: 本地服务暂时不可用，先按本地剧情继续。";
            if (instance_exists(room_text))
            {
                with (room_text)
                    unlocked = "本地服务暂时不可用，已切换本地剧情；仍可去酒吧上班。";
            }
        }
        if (ds_exists(ag_preload_root, ds_type_map)) ds_map_destroy(ag_preload_root);
    }
    else
    {
        ag_preload_state = 2;
        if (ag_preload_http_status == 503)
            ag_preload_error = "O.S.：剧情服务暂时不可用，已切换本地剧情。";
        else if (ag_preload_http_status == 429)
            ag_preload_error = "O.S.：API额度不足，已切换本地剧情。";
        else
            ag_preload_error = "O.S.：本地世界服务未响应，已切换本地剧情。";
        global.jillcomment = "JILL: 本地服务暂时不可用，先按本地剧情继续。";
        if (instance_exists(room_text))
        {
            with (room_text)
                unlocked = "本地服务暂时不可用，已切换本地剧情；仍可去酒吧上班。";
        }
    }
}
