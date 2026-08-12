ag_state = 0;
ag_request_id = "open_" + string(current_time);
ag_scene_id = "";
ag_line_index = 0;
ag_error_message = "";
ag_timeout_at = current_time + 5000;

// The launcher writes the ephemeral token and port to a local runtime file.
// The final patch script replaces this placeholder with the verified reader.
ag_bridge_url = "http://127.0.0.1:8711/v1/scenes/open";
