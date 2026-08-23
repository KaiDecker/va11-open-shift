ag_state = 0;
ag_http_request = -1;
ag_scene_job_id = "";
ag_scene_job_poll_at = 0;
ag_scene_job_poll_count = 0;
ag_request_sequence = 1;
global.ag_request_epoch += 1;
ag_request_scope = string(global.ag_request_epoch);
ag_scene_id = "";
ag_line_index = 0;
ag_line_count = 0;
ag_return_to = "";
ag_timeout_at = current_time + 120000;
ag_line_active = 0;
ag_wait_box = noone;
ag_wait_speaker = "";
ag_wait_started_at = 0;
ag_portrait_speaker = "";
ag_error_message = "";
ag_order_pending = 0;
ag_order_started = 0;
ag_order_id = "";
ag_order_customer = "";
ag_order_display_text = "";

ini_open("open-shift-runtime.ini");
ag_bridge_port = ini_read_real("bridge", "port", 8711);
ag_bridge_token = ini_read_string("bridge", "token", "");
ag_session_id = ini_read_string("bridge", "session_id", "");
ini_close();

if (ag_bridge_port < 1 || ag_bridge_port > 65535 || string_length(ag_bridge_token) < 16 || string_length(ag_session_id) < 16)
{
    ag_error_message = "O.S.：运行配置缺失或无效。";
    ag_state = 4;
}
else
{
    ag_bridge_url = "http://127.0.0.1:" + string(ag_bridge_port);
    var ag_headers;
    var ag_body;
    ag_headers = ds_map_create();
    ds_map_add(ag_headers, "Content-Type", "application/json");
    ds_map_add(ag_headers, "X-Open-Shift-Token", ag_bridge_token);
    ag_body = ds_map_create();
    ag_request_id = "open_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_request_sequence);
    ds_map_add(ag_body, "protocol_version", 1);
    ds_map_add(ag_body, "request_id", ag_request_id);
    ds_map_add(ag_body, "client_session_id", ag_session_id);
    ag_http_request = http_request(ag_bridge_url + "/v1/scenes/jobs", "POST", ag_headers, json_encode(ag_body));
    ds_map_destroy(ag_body);
    ds_map_destroy(ag_headers);
    ag_bridge_token = "";
    ag_state = 1;
}

