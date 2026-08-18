if (!ag_sent && ag_operation != "")
{
    if ((ag_operation != "pair" && ag_operation != "restore") || ag_slot < 1 || ag_slot > 24 || floor(ag_slot) != ag_slot)
    {
        ag_error_message = "O.S.：存档槽位请求无效。";
        ag_state = 2;
    }
    else
    {
        ini_open("open-shift-runtime.ini");
        var ag_bridge_port;
        var ag_bridge_token;
        var ag_session_id;
        ag_bridge_port = ini_read_real("bridge", "port", 8711);
        ag_bridge_token = ini_read_string("bridge", "token", "");
        ag_session_id = ini_read_string("bridge", "session_id", "");
        ini_close();
        if (ag_bridge_port < 1 || ag_bridge_port > 65535 || string_length(ag_bridge_token) < 16 || string_length(ag_session_id) < 16)
        {
            ag_error_message = "O.S.：存档服务运行配置无效。";
            ag_state = 2;
        }
        else
        {
            var ag_headers;
            var ag_body;
            var ag_bridge_url;
            ag_headers = ds_map_create();
            ds_map_add(ag_headers, "Content-Type", "application/json");
            ds_map_add(ag_headers, "X-Open-Shift-Token", ag_bridge_token);
            ag_body = ds_map_create();
            ag_request_id = ag_operation + "_" + ag_session_id + "_" + string(ag_slot) + "_" + string(id);
            ds_map_add(ag_body, "protocol_version", 1);
            ds_map_add(ag_body, "request_id", ag_request_id);
            ds_map_add(ag_body, "client_session_id", ag_session_id);
            ds_map_add(ag_body, "slot", ag_slot);
            ag_bridge_url = "http://127.0.0.1:" + string(ag_bridge_port);
            ag_http_request = http_request(ag_bridge_url + "/v1/saves/" + ag_operation, "POST", ag_headers, json_encode(ag_body));
            ds_map_destroy(ag_body);
            ds_map_destroy(ag_headers);
            ag_bridge_token = "";
            ag_sent = 1;
            ag_state = 1;
            ag_timeout_at = current_time + 30000;
        }
    }
}

if (ag_state == 1 && current_time > ag_timeout_at)
{
    ag_error_message = "O.S.：配对存档服务没有响应。";
    ag_state = 2;
}

if (ag_state == 2 && !instance_exists(obj_textbox))
{
    var ag_error_box;
    ag_error_box = instance_create(0, 0, obj_textbox);
    ag_error_box.current_text = 0;
    ag_error_box.current_chr = 0;
    ag_error_box.current_line = 0;
    ag_error_box.total_boxes = 0;
    ag_error_box.input_text[0] = ag_error_message;
    ag_error_box.edited_text[0] = ag_error_message;
    ag_error_box.cmd_data_queue = ds_queue_create();
    ag_error_box.cmd_pos_queue = ds_queue_create();
    ag_error_box.next_cmd_pos = -1;
    ag_error_box.textbox_skip_possible = 1;
    global.output_text = "";
    ag_state = 3;
}

if (ag_state == 3 && !instance_exists(obj_textbox))
{
    global.block_click = 0;
    instance_destroy();
}
