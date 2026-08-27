ag_state = 0;
ag_http_request = -1;
// Fire-and-forget client diagnostics use a separate request handle so they
// can never consume or block the gameplay HTTP state machine.
ag_diag_http_request = -1;
ag_diag_request_sequence = 0;
ag_scene_job_id = "";
ag_scene_job_poll_at = 0;
ag_scene_job_poll_count = 0;
ag_order_job_id = "";
ag_order_job_poll_at = 0;
ag_order_job_poll_count = 0;
ag_request_sequence = 1;
global.ag_request_epoch += 1;
ag_request_scope = string(global.ag_request_epoch);
ag_scene_id = "";
ag_line_index = 0;
ag_line_count = 0;
ag_portrait[0] = "";
ag_speaker[0] = "";
ag_expression[0] = "neutral";
ag_text[0] = "";
ag_display_name[0] = "";
ag_name_color[0] = 13;
ag_return_to = "";
ag_timeout_at = current_time + 120000;
ag_line_active = 0;
ag_wait_box = noone;
ag_wait_speaker = "";
ag_wait_started_at = 0;
ag_portrait_speaker = "";
ag_error_message = "";
ag_last_http_status = -1;
ag_last_transport_status = -1;
ag_last_phase = "init";
ag_last_job_id = "";
ag_order_pending = 0;
ag_order_started = 0;
ag_order_id = "";
ag_order_customer = "";
ag_order_display_text = "";
ag_music_gate_active = 0;
// The vanilla break flow stops music and reopens the jukebox after the save
// page. This flag distinguishes that second gate from the first selection.
ag_music_resume_pending = 0;
ag_break_wait_logged = 0;
// A break scene is acknowledged while still in the bar.  Only that ACK's
// successful callback may enter the vanilla break_time room.
ag_break_enter_after_ack = 0;
// Track the native break room so only a real return to the bar clears the
// Open Shift hand-off and lets dialog_control resume the vanilla cursor.
ag_break_room_entered = 0;
ag_break_returned = 0;
ag_last_room = -1;
// Dynamic dialogue is supplied to the original textbox through this
// short-lived memory queue. The queue is consumed synchronously by
// textbox_loadbox before the flag is cleared by the caller.
global.ag_memory_textbox_active = 0;
global.ag_memory_textbox_line_count = 0;
global.ag_memory_textbox_lines[0] = "";
global.ag_memory_textbox_wait = 0;

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

