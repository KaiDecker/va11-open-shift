ag_state = 0;
ag_http_request = -1;
ag_request_id = "open_" + string(current_time);
ag_session_id = "game_" + string(current_time);
ag_scene_id = "";
ag_line_index = 0;
ag_line_count = 0;
ag_return_to = "";
ag_timeout_at = current_time + 5000;
ag_message = noone;

ini_open("open-shift-runtime.ini");
ag_bridge_port = ini_read_real("bridge", "port", 8711);
ag_bridge_token = ini_read_string("bridge", "token", "");
ini_close();

if (ag_bridge_port < 1 || ag_bridge_port > 65535 || string_length(ag_bridge_token) < 16)
{
    ag_error_message = "Open Shift runtime configuration is missing or invalid.";
    ag_state = 4;
    ag_message = instance_create(0, 0, ag_safe_text);
    ag_message.ag_speaker = "OPEN SHIFT";
    ag_message.ag_text = ag_error_message;
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
    ds_map_add(ag_body, "protocol_version", 1);
    ds_map_add(ag_body, "request_id", ag_request_id);
    ds_map_add(ag_body, "client_session_id", ag_session_id);
    ag_http_request = http_request(ag_bridge_url + "/v1/scenes/open", "POST", ag_headers, json_encode(ag_body));
    ds_map_destroy(ag_body);
    ds_map_destroy(ag_headers);
    ag_bridge_token = "";
    ag_state = 1;
    ag_message = instance_create(0, 0, ag_safe_text);
    ag_message.ag_speaker = "OPEN SHIFT";
    ag_message.ag_text = "Connecting to the local world service...";
}

