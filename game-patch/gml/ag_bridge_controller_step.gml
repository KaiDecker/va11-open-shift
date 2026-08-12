if ((ag_state == 1 || ag_state == 3) && current_time > ag_timeout_at)
{
    ag_message.ag_speaker = "OPEN SHIFT";
    if (ag_state == 3)
    {
        global.block_click = 0;
        instance_create(0, 0, out_to_title);
        with (ag_message) instance_destroy();
        instance_destroy();
    }
    else
    {
        ag_state = 4;
        ag_message.ag_speaker = "OPEN SHIFT";
        ag_message.ag_text = "The local world service did not respond.";
    }
}

if (ag_state == 2 && mouse_check_button_pressed(mb_left))
{
    ag_line_index += 1;
    if (ag_line_index < ag_line_count)
    {
        ag_message.ag_speaker = ag_speaker[ag_line_index];
        ag_message.ag_text = ag_text[ag_line_index];
        ag_message.ag_portrait = ag_portrait[ag_line_index];
    }
    else
    {
        var ag_headers;
        var ag_body;
        ag_headers = ds_map_create();
        ds_map_add(ag_headers, "Content-Type", "application/json");
        ini_open("open-shift-runtime.ini");
        ds_map_add(ag_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
        ini_close();
        ag_body = ds_map_create();
        ds_map_add(ag_body, "protocol_version", 1);
        ds_map_add(ag_body, "request_id", "ack_" + string(current_time));
        ds_map_add(ag_body, "client_session_id", ag_session_id);
        ds_map_add(ag_body, "scene_id", ag_scene_id);
        ds_map_add(ag_body, "outcome", "returned_to_title");
        ag_http_request = http_request(ag_bridge_url + "/v1/scenes/ack", "POST", ag_headers, json_encode(ag_body));
        ds_map_destroy(ag_body);
        ds_map_destroy(ag_headers);
        ag_timeout_at = current_time + 3000;
        ag_state = 3;
        ag_message.ag_speaker = "OPEN SHIFT";
        ag_message.ag_text = "Returning to the title screen...";
    }
}

if (ag_state == 4 && mouse_check_button_pressed(mb_left))
{
    global.block_click = 0;
    instance_create(0, 0, out_to_title);
    with (ag_message) instance_destroy();
    instance_destroy();
}
