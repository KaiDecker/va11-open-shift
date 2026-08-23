if (ds_map_find_value(async_load, "id") == ag_http_request)
{
    var ag_status;
    var ag_http_status;
    var ag_result;
    ag_status = ds_map_find_value(async_load, "status");
    ag_http_status = ds_map_find_value(async_load, "http_status");
    ag_result = ds_map_find_value(async_load, "result");
    if (ag_status != 0 || ag_http_status != 200)
    {
        if (ag_operation == "restore" && ag_http_status == 409)
            ag_error_message = "O.S.：原版存档与Agent世界不匹配，已拒绝读取。";
        else if (ag_operation == "restore")
            ag_error_message = "O.S.：Agent存档无法恢复，已拒绝读取。";
        else
            ag_error_message = "O.S.：Agent存档失败，上一份配对存档已保留。";
        ag_state = 2;
    }
    else
    {
        var ag_root;
        var ag_valid;
        var ag_response_world_day;
        ag_root = json_decode(ag_result);
        ag_valid = true;
        ag_response_world_day = 1;
        if (!ds_exists(ag_root, ds_type_map) || ds_map_size(ag_root) != 6)
            ag_valid = false;
        if (ag_valid && (!ds_map_exists(ag_root, "protocol_version") || ds_map_find_value(ag_root, "protocol_version") != 1))
            ag_valid = false;
        if (ag_valid && (!ds_map_exists(ag_root, "request_id") || ds_map_find_value(ag_root, "request_id") != ag_request_id))
            ag_valid = false;
        if (ag_valid && (!ds_map_exists(ag_root, "slot") || ds_map_find_value(ag_root, "slot") != ag_slot))
            ag_valid = false;
        if (ag_valid && (!ds_map_exists(ag_root, "revision") || string_length(ds_map_find_value(ag_root, "revision")) != 32))
            ag_valid = false;
        if (ag_valid && (!ds_map_exists(ag_root, "world_day") || ds_map_find_value(ag_root, "world_day") < 1))
            ag_valid = false;
        if (ag_valid)
            ag_response_world_day = ds_map_find_value(ag_root, "world_day");
        var ag_expected_status;
        if (ag_operation == "pair")
            ag_expected_status = "paired";
        else
            ag_expected_status = "restored";
        if (ag_valid && (!ds_map_exists(ag_root, "status") || ds_map_find_value(ag_root, "status") != ag_expected_status))
            ag_valid = false;
        if (ds_exists(ag_root, ds_type_map))
            ds_map_destroy(ag_root);
        if (!ag_valid)
        {
            ag_error_message = "O.S.：配对存档服务返回了无效响应。";
            ag_state = 2;
        }
        else if (ag_operation == "restore")
        {
            global.block_click = 0;
            instance_create(x, y, out_to_loading);
            var ag_loader;
            ag_loader = instance_create(x, y, loader_control);
            ag_loader.load_slot = ag_slot;
            instance_destroy();
        }
        else
        {
            global.ag_story_day = ag_response_world_day;
            global.datestring = "O.S. DAY " + string(global.ag_story_day);
            global.jillcomment = "O.S.：已保存到 DAY " + string(global.ag_story_day) + "。";
            global.block_click = 0;
            instance_destroy();
        }
    }
}
