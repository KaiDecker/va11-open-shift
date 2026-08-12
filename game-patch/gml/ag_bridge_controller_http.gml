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
        if (ag_state == 3)
        {
            ag_state = 4;
            ag_error_message = "O.S.：本地世界服务拒绝了场景确认。";
        }
        else
        {
            ag_state = 4;
            ag_error_message = "O.S.：本地世界服务拒绝了请求。";
        }
    }
    else if (ag_state == 3)
    {
        ag_request_sequence += 1;
        ag_request_id = "open_" + ag_session_id + "_" + string(ag_request_sequence);
        var ag_next_headers;
        var ag_next_body;
        ag_next_headers = ds_map_create();
        ds_map_add(ag_next_headers, "Content-Type", "application/json");
        ini_open("open-shift-runtime.ini");
        ds_map_add(ag_next_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
        ini_close();
        ag_next_body = ds_map_create();
        ds_map_add(ag_next_body, "protocol_version", 1);
        ds_map_add(ag_next_body, "request_id", ag_request_id);
        ds_map_add(ag_next_body, "client_session_id", ag_session_id);
        ag_http_request = http_request(ag_bridge_url + "/v1/scenes/open", "POST", ag_next_headers, json_encode(ag_next_body));
        ds_map_destroy(ag_next_body);
        ds_map_destroy(ag_next_headers);
        ag_timeout_at = current_time + 5000;
        ag_state = 1;
    }
    else if (ag_state == 1)
    {
        var ag_root;
        var ag_scene;
        var ag_lines;
        var ag_valid;
        ag_valid = true;
        ag_root = json_decode(ag_result);

        if (!ds_exists(ag_root, ds_type_map) || ds_map_size(ag_root) != 3)
            ag_valid = false;
        if (ag_valid && (!ds_map_exists(ag_root, "protocol_version") || ds_map_find_value(ag_root, "protocol_version") != 1))
            ag_valid = false;
        if (ag_valid && (!ds_map_exists(ag_root, "request_id") || ds_map_find_value(ag_root, "request_id") != ag_request_id))
            ag_valid = false;
        if (ag_valid && !ds_map_exists(ag_root, "scene"))
            ag_valid = false;

        if (ag_valid)
        {
            ag_scene = ds_map_find_value(ag_root, "scene");
            if (!ds_exists(ag_scene, ds_type_map) || ds_map_size(ag_scene) != 3)
                ag_valid = false;
        }
        if (ag_valid && (!ds_map_exists(ag_scene, "scene_id") || !ds_map_exists(ag_scene, "lines") || !ds_map_exists(ag_scene, "return_to")))
            ag_valid = false;
        if (ag_valid)
        {
            ag_scene_id = ds_map_find_value(ag_scene, "scene_id");
            ag_return_to = ds_map_find_value(ag_scene, "return_to");
            ag_lines = ds_map_find_value(ag_scene, "lines");
            if ((ag_scene_id != "stage_3_connection_test" && string_copy(ag_scene_id, 1, 12) != "world_event_") || ag_return_to != "bar" || !ds_exists(ag_lines, ds_type_list) || ds_list_size(ag_lines) != 3)
                ag_valid = false;
        }

        if (ag_valid)
        {
            ag_line_count = 3;
            for (var ag_i = 0; ag_i < ag_line_count; ag_i += 1)
            {
                var ag_line;
                var ag_speaker_id;
                var ag_portrait_id;
                var ag_expression_id;
                var ag_line_text;
                ag_line = ds_list_find_value(ag_lines, ag_i);
                if (!ds_exists(ag_line, ds_type_map) || ds_map_size(ag_line) != 5)
                {
                    ag_valid = false;
                    break;
                }
                if (!ds_map_exists(ag_line, "line_id") || !ds_map_exists(ag_line, "speaker_id") || !ds_map_exists(ag_line, "portrait_id") || !ds_map_exists(ag_line, "expression_id") || !ds_map_exists(ag_line, "text"))
                {
                    ag_valid = false;
                    break;
                }
                ag_speaker_id = ds_map_find_value(ag_line, "speaker_id");
                ag_portrait_id = ds_map_find_value(ag_line, "portrait_id");
                ag_expression_id = ds_map_find_value(ag_line, "expression_id");
                ag_line_text = ds_map_find_value(ag_line, "text");
                if (ag_speaker_id != "dana" && ag_speaker_id != "dorothy" && ag_speaker_id != "alma" && ag_speaker_id != "stella" && ag_speaker_id != "sei")
                    ag_valid = false;
                if (ag_portrait_id != "sprite_dana" && ag_portrait_id != "sprite_doro" && ag_portrait_id != "sprite_alma" && ag_portrait_id != "sprite_stella" && ag_portrait_id != "sprite_sei")
                    ag_valid = false;
                if ((ag_speaker_id == "dana" && ag_portrait_id != "sprite_dana") || (ag_speaker_id == "dorothy" && ag_portrait_id != "sprite_doro") || (ag_speaker_id == "alma" && ag_portrait_id != "sprite_alma") || (ag_speaker_id == "stella" && ag_portrait_id != "sprite_stella") || (ag_speaker_id == "sei" && ag_portrait_id != "sprite_sei"))
                    ag_valid = false;
                if (ag_expression_id != "neutral" && ag_expression_id != "happy" && ag_expression_id != "worry" && ag_expression_id != "playful")
                    ag_valid = false;
                if (string_length(ag_line_text) < 1 || string_length(ag_line_text) > 240)
                    ag_valid = false;
                if (!ag_valid)
                    break;
                ag_speaker[ag_i] = ag_speaker_id;
                ag_text[ag_i] = ag_line_text;
                ag_expression[ag_i] = ag_expression_id;
                ag_display_name[ag_i] = string_upper(ag_speaker_id);
                if (ag_speaker_id == "dorothy") ag_display_name[ag_i] = "DOROTHY";
                ag_name_color[ag_i] = 15;
                if (ag_speaker_id == "alma") ag_name_color[ag_i] = 14;
                if (ag_speaker_id == "dana") ag_name_color[ag_i] = 15;
                if (ag_speaker_id == "stella") ag_name_color[ag_i] = 16;
                if (ag_speaker_id == "sei") ag_name_color[ag_i] = 17;
                if (ag_speaker_id == "dorothy") ag_name_color[ag_i] = 18;
            }
        }

        if (ds_exists(ag_root, ds_type_map))
            ds_map_destroy(ag_root);

        if (ag_valid)
        {
            ag_line_index = 0;
            ag_state = 2;
        }
        else
        {
            ag_state = 4;
            ag_error_message = "O.S.：本地世界服务返回了无效场景。";
        }
    }
}
