if ((ag_state == 1 || ag_state == 3 || ag_state == 7) && current_time > ag_timeout_at)
{
    if (ag_state == 3)
    {
        ag_state = 4;
        ag_error_message = "O.S.：本地世界服务没有确认场景结果。";
    }
    else if (ag_state == 7)
    {
        ag_state = 4;
        ag_error_message = "O.S.：本地世界服务没有返回调酒结果。";
    }
    else
    {
        ag_state = 4;
        ag_error_message = "O.S.：本地世界服务没有响应。";
    }
}

if ((ag_state == 1 || ag_state == 7) && !instance_exists(obj_textbox))
{
    ag_wait_box = instance_create(0, 0, obj_textbox);
    ag_wait_box.current_text = 0;
    ag_wait_box.current_chr = 0;
    ag_wait_box.current_line = 0;
    ag_wait_box.total_boxes = 0;
    if (ag_state == 7)
        ag_wait_box.input_text[0] = "调酒杯在吧台上轻轻落定。";
    else
        ag_wait_box.input_text[0] = "冰箱压缩机在吧台后低声运转。";
    ag_wait_box.edited_text[0] = ag_wait_box.input_text[0];
    ag_wait_box.cmd_data_queue = ds_queue_create();
    ag_wait_box.cmd_pos_queue = ds_queue_create();
    ag_wait_box.next_cmd_pos = -1;
    ag_wait_box.textbox_skip_possible = 0;
    global.output_text = "";
}

if (ag_state == 2 && !instance_exists(obj_textbox))
{
    if (ag_line_active)
    {
        reset_lips();
        ag_line_active = 0;
        ag_line_index += 1;
    }

    if (ag_line_index < ag_line_count)
    {
        var ag_current_speaker;
        var ag_current_expression;
        ag_current_speaker = ag_speaker[ag_line_index];
        ag_current_expression = ag_expression[ag_line_index];

        if (ag_current_speaker != "jill" && ag_current_speaker != "")
        {
            global.danahide = 0;
            global.dorohide = 0;
            global.almahide = 0;
            global.stelhide = 0;
            global.seihide = 0;
            global.danaface = "";
            global.doroface = "";
            global.almaface = "";
            global.stelface = "";
            global.seiface = "";

            if (ag_current_speaker != ag_portrait_speaker)
            {
                with (sprite_dana) instance_destroy();
                with (sprite_doro) instance_destroy();
                with (sprite_alma) instance_destroy();
                with (sprite_stella) instance_destroy();
                with (sprite_sei) instance_destroy();
                ag_portrait_speaker = ag_current_speaker;
            }
        }

        if (ag_current_speaker == "dana")
        {
            if (ag_current_expression == "happy") global.danaface = "closedsmile";
            if (ag_current_expression == "worry") global.danaface = "worry";
            if (ag_current_expression == "playful") global.danaface = "eee";
            global.danalips = 1;
            if (!instance_exists(sprite_dana)) instance_create(185, 268, sprite_dana);
        }
        else if (ag_current_speaker == "dorothy")
        {
            if (ag_current_expression == "happy") global.doroface = "pachi";
            if (ag_current_expression == "worry") global.doroface = "sad";
            if (ag_current_expression == "playful") global.doroface = "smug";
            global.dorolips = 1;
            if (!instance_exists(sprite_doro)) instance_create(185, 268, sprite_doro);
        }
        else if (ag_current_speaker == "alma")
        {
            if (ag_current_expression == "happy") global.almaface = "smile";
            if (ag_current_expression == "worry") global.almaface = "worried";
            if (ag_current_expression == "playful") global.almaface = "smug";
            global.almalips = 1;
            if (!instance_exists(sprite_alma)) instance_create(185, 268, sprite_alma);
        }
        else if (ag_current_speaker == "stella")
        {
            if (ag_current_expression == "happy") global.stelface = "happy";
            if (ag_current_expression == "worry") global.stelface = "concern";
            if (ag_current_expression == "playful") global.stelface = "baka";
            global.stellips = 1;
            if (!instance_exists(sprite_stella)) instance_create(185, 268, sprite_stella);
        }
        else if (ag_current_speaker == "sei")
        {
            if (ag_current_expression == "happy" || ag_current_expression == "playful") global.seiface = "smile";
            if (ag_current_expression == "worry") global.seiface = "worried";
            global.seilips = 1;
            if (!instance_exists(sprite_sei)) instance_create(185, 268, sprite_sei);
        }
        var ag_textbox;
        var ag_raw_text;
        var ag_wrapped_text;
        var ag_line_buffer;
        if (ag_display_name[ag_line_index] == "")
            ag_raw_text = ag_text[ag_line_index];
        else
            ag_raw_text = ag_display_name[ag_line_index] + ": " + ag_text[ag_line_index];
        ag_wrapped_text = "";
        ag_line_buffer = "";
        draw_set_font(global.fnt_textbox);
        for (var ag_char_i = 1; ag_char_i <= string_length(ag_raw_text); ag_char_i += 1)
        {
            var ag_next_char;
            var ag_wrap_candidate;
            ag_next_char = string_char_at(ag_raw_text, ag_char_i);
            ag_wrap_candidate = ag_line_buffer + ag_next_char;
            if (string_length(ag_line_buffer) > 0 && string_width(ag_wrap_candidate) > 380)
            {
                if (string_length(ag_wrapped_text) > 0) ag_wrapped_text += "#";
                ag_wrapped_text += ag_line_buffer;
                ag_line_buffer = ag_next_char;
            }
            else
            {
                ag_line_buffer = ag_wrap_candidate;
            }
        }
        if (string_length(ag_wrapped_text) > 0) ag_wrapped_text += "#";
        ag_wrapped_text += ag_line_buffer;
        ag_textbox = instance_create(0, 0, obj_textbox);
        ag_textbox.current_text = 0;
        ag_textbox.current_chr = 0;
        ag_textbox.current_line = 0;
        ag_textbox.total_boxes = 0;
        ag_textbox.input_text[0] = ag_wrapped_text;
        ag_textbox.edited_text[0] = ag_textbox.input_text[0];
        ag_textbox.cmd_data_queue = ds_queue_create();
        ag_textbox.cmd_pos_queue = ds_queue_create();
        if (ag_display_name[ag_line_index] != "")
        {
            ds_queue_enqueue(ag_textbox.cmd_pos_queue, 0);
            ds_queue_enqueue(ag_textbox.cmd_data_queue, "C:" + string(ag_name_color[ag_line_index]));
            ds_queue_enqueue(ag_textbox.cmd_pos_queue, string_length(ag_display_name[ag_line_index]) + 1);
            ds_queue_enqueue(ag_textbox.cmd_data_queue, "C:C");
            ag_textbox.next_cmd_pos = ds_queue_head(ag_textbox.cmd_pos_queue);
        }
        else
            ag_textbox.next_cmd_pos = -1;
        ag_textbox.textbox_skip_possible = 1;
        global.output_text = "";
        ag_line_active = 1;
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
        ds_map_add(ag_body, "request_id", "ack_" + ag_session_id + "_" + string(ag_request_sequence));
        ds_map_add(ag_body, "client_session_id", ag_session_id);
        ds_map_add(ag_body, "scene_id", ag_scene_id);
        if (ag_order_pending)
            ds_map_add(ag_body, "outcome", "order_started");
        else
            ds_map_add(ag_body, "outcome", "continued_in_bar");
        ag_http_request = http_request(ag_bridge_url + "/v1/scenes/ack", "POST", ag_headers, json_encode(ag_body));
        ds_map_destroy(ag_body);
        ds_map_destroy(ag_headers);
        ag_timeout_at = current_time + 3000;
        ag_state = 3;
    }
}

if (ag_state == 4 && !instance_exists(obj_textbox))
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
    ag_state = 5;
}

if (ag_state == 5 && !instance_exists(obj_textbox))
{
    global.block_click = 0;
    instance_destroy();
}
