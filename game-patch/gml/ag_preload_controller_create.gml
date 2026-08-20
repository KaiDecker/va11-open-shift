ag_preload_state = 0;
ag_preload_http_request = -1;
ag_preload_request_id = "";
ag_preload_timeout_at = current_time + 120000;
ag_preload_error = "";
ag_preload_attempt = 1;
global.ag_prefetch_ready = 0;
global.ag_prefetch_failed = 0;

ini_open("open-shift-runtime.ini");
ag_preload_port = ini_read_real("bridge", "port", 8711);
ag_preload_token = ini_read_string("bridge", "token", "");
ag_preload_session = ini_read_string("bridge", "session_id", "");
ini_close();

if (ag_preload_port < 1 || ag_preload_port > 65535 || string_length(ag_preload_token) < 16 || string_length(ag_preload_session) < 16)
{
    ag_preload_error = "O.S.：运行配置缺失或无效。";
    ag_preload_state = 3;
}
else
{
    var ag_preload_headers;
    var ag_preload_body;
    ag_preload_headers = ds_map_create();
    ds_map_add(ag_preload_headers, "Content-Type", "application/json");
    ds_map_add(ag_preload_headers, "X-Open-Shift-Token", ag_preload_token);
    ag_preload_body = ds_map_create();
    ag_preload_request_id = "prepare_" + ag_preload_session + "_" + string(ag_preload_attempt);
    ds_map_add(ag_preload_body, "protocol_version", 1);
    ds_map_add(ag_preload_body, "request_id", ag_preload_request_id);
    ds_map_add(ag_preload_body, "client_session_id", ag_preload_session);
    ag_preload_http_request = http_request("http://127.0.0.1:" + string(ag_preload_port) + "/v1/story/prepare", "POST", ag_preload_headers, json_encode(ag_preload_body));
    ds_map_destroy(ag_preload_body);
    ds_map_destroy(ag_preload_headers);
    ag_preload_token = "";
    ag_preload_timeout_at = current_time + 120000;
    ag_preload_state = 1;
}
