if (ds_map_find_value(async_load, "id") == ag_http_request)
{
    var ag_status;
    var ag_http_status;
    var ag_result;
    var ag_was_order_response;
    var ag_error_code;
    var ag_scene_job_deferred;
    var ag_has_status;
    var ag_has_http_status;
    var ag_has_result;
    var ag_result_is_json;
    var ag_result_has_job_id;
    var ag_result_has_scene;
    var ag_result_empty;
    var ag_http_compat;
    var ag_http_ready;
    var ag_result_probe;
    var ag_safe_error_code;
    ag_has_status = ds_map_exists(async_load, "status");
    ag_has_http_status = ds_map_exists(async_load, "http_status");
    ag_has_result = ds_map_exists(async_load, "result");
    ag_status = -1;
    ag_http_status = -1;
    ag_result = "";
    if (ag_has_status)
        ag_status = ds_map_find_value(async_load, "status");
    if (ag_has_http_status)
        ag_http_status = ds_map_find_value(async_load, "http_status");
    if (ag_has_result)
        ag_result = ds_map_find_value(async_load, "result");
    ag_result_is_json = 0;
    ag_result_has_job_id = 0;
    ag_result_has_scene = 0;
    ag_result_empty = (string_length(string(ag_result)) == 0);
    if (string_length(string(ag_result)) > 0)
    {
        ag_result_probe = json_decode(ag_result);
        if (ds_exists(ag_result_probe, ds_type_map))
        {
            ag_result_is_json = 1;
            ag_result_has_job_id = ds_map_exists(ag_result_probe, "job_id");
            ag_result_has_scene = ds_map_exists(ag_result_probe, "scene");
        }
        if (ds_exists(ag_result_probe, ds_type_map))
            ds_map_destroy(ag_result_probe);
    }
    // Some GameMaker runtimes omit http_status on later callbacks even when
    // transport succeeded. A valid JSON object is safe to route to the
    // existing result parser; malformed or empty payloads still fail closed.
    // A few older runtimes include http_status but leave it at -1 even when
    // transport succeeded. Treat a valid JSON response as usable in that
    // case; malformed/error payloads still go through normal validation.
    ag_http_compat = (ag_status == 0 && ag_result_is_json && (!ag_has_http_status || ag_http_status <= 0));
    ag_http_ready = (ag_status == 0 && (ag_http_status == 200 || ag_http_compat));
    ag_last_http_status = ag_http_status;
    ag_last_transport_status = ag_status;
    if (ag_state == 7 || ag_state == 11)
        ag_last_phase = "order_poll";
    else if (ag_state == 8)
        ag_last_phase = "scene_poll";
    else if (ag_state == 3)
        ag_last_phase = "ack";
    else
        ag_last_phase = "scene_open";
    show_debug_message("[OPEN SHIFT] dialogue_callback fields=status:" + string(ag_has_status) + ",http_status:" + string(ag_has_http_status) + ",result:" + string(ag_has_result) + " http_status=" + string(ag_http_status) + " transport_status=" + string(ag_status) + " request=" + ag_request_id + " phase=" + ag_last_phase + " state=" + string(ag_state));
    if (ag_state == 3 && !ag_http_ready)
        show_debug_message("[OPEN SHIFT] scene_ack_failed http_status=" + string(ag_http_status) + " transport_status=" + string(ag_status) + " result=" + string(ag_result));
    ag_was_order_response = (ag_state == 7 || ag_state == 11);
    ag_error_code = "";
    ag_safe_error_code = "";
    ag_scene_job_deferred = 0;

    // Some older GameMaker runtimes report a successful POST with only
    // status=0 and no response body. The server derives a deterministic job
    // id from request_id, so continue with polling instead of failing.
    if (ag_state == 7 && ag_status == 0 && ag_result_empty)
    {
        ag_order_job_id = "order_job_" + ag_request_id;
        ag_last_job_id = ag_order_job_id;
        ag_order_job_poll_count = 0;
        ag_http_request = -1;
        ag_state = 11;
        ag_order_job_poll_at = current_time + 750;
        ag_scene_job_deferred = 1;
        show_debug_message("[OPEN SHIFT] order_job_compat_accepted job=" + ag_order_job_id + " request=" + ag_request_id);
    }
    else if (ag_state == 11 && ag_status == 0 && ag_result_empty)
    {
        // An empty poll callback is a runtime quirk, not a terminal failure.
        ag_http_request = -1;
        ag_order_job_poll_at = current_time + 750;
        ag_scene_job_deferred = 1;
        show_debug_message("[OPEN SHIFT] order_job_compat_poll_empty job=" + ag_order_job_id + " request=" + ag_request_id);
    }

    // The job endpoint acknowledges quickly (202). Keep the existing scene
    // decoder for the final legacy-shaped result, so old validation remains
    // authoritative while the game polls in the background.
    if (ag_status == 0 && (ag_http_status == 202 || (ag_http_compat && ag_result_has_job_id)) && (ag_state == 1 || ag_state == 7 || ag_state == 8 || ag_state == 11))
    {
        var ag_job_root;
        ag_job_root = json_decode(ag_result);
        if (ds_exists(ag_job_root, ds_type_map) && ds_map_exists(ag_job_root, "job_id"))
        {
            if (ag_state == 1)
            {
                ag_scene_job_id = string(ds_map_find_value(ag_job_root, "job_id"));
                ag_scene_job_poll_count = 0;
                if (ds_map_exists(ag_job_root, "speaker_hint"))
                    ag_wait_speaker = string(ds_map_find_value(ag_job_root, "speaker_hint"));
                show_debug_message("[OPEN SHIFT] dialogue_job_queued job=" + ag_scene_job_id);
            }
            else if (ag_state == 7)
            {
                ag_order_job_id = string(ds_map_find_value(ag_job_root, "job_id"));
                ag_last_job_id = ag_order_job_id;
                ag_order_job_poll_count = 0;
                show_debug_message("[OPEN SHIFT] order_job_queued job=" + ag_order_job_id);
            }
            else if (ag_state == 8 || ag_state == 11)
            {
                // A poll can also return 202 while the worker is still running.
                // Keep the existing job id and continue polling instead of
                // falling through to the generic order failure message.
                if (ag_state == 11)
                    show_debug_message("[OPEN SHIFT] order_job_pending job=" + ag_order_job_id);
                else
                    show_debug_message("[OPEN SHIFT] dialogue_job_pending job=" + ag_scene_job_id);
            }
            ag_http_request = -1;
            if (ag_state == 7 || ag_state == 11)
            {
                ag_state = 11;
                ag_order_job_poll_at = current_time + 750;
            }
            else
            {
                ag_state = 8;
                ag_scene_job_poll_at = current_time + 750;
            }
            ag_scene_job_deferred = 1;
        }
        if (ds_exists(ag_job_root, ds_type_map)) ds_map_destroy(ag_job_root);
    }
    else if (ag_http_ready && (ag_state == 8 || ag_state == 11))
    {
        var ag_job_result_root;
        ag_job_result_root = json_decode(ag_result);
        // A ready /result response is deliberately the old three-field
        // scene envelope; let the normal parser below consume it.
        if (!ds_exists(ag_job_result_root, ds_type_map) || !ds_map_exists(ag_job_result_root, "scene"))
        {
            ag_http_request = -1;
            if (ag_state == 11)
                ag_order_job_poll_at = current_time + 750;
            else
                ag_scene_job_poll_at = current_time + 750;
            ag_scene_job_deferred = 1;
        }
        else
        {
            if (ag_state == 11)
            {
                ag_state = 7;
                show_debug_message("[OPEN SHIFT] order_job_ready job=" + ag_order_job_id);
            }
            else
            {
                ag_state = 1;
                show_debug_message("[OPEN SHIFT] dialogue_job_ready job=" + ag_scene_job_id);
            }
        }
        if (ds_exists(ag_job_result_root, ds_type_map)) ds_map_destroy(ag_job_result_root);
    }

    if (ag_scene_job_deferred == 0 && (ag_state == 1 || ag_state == 7 || ag_state == 8 || ag_state == 11) && instance_exists(ag_wait_box))
    {
        with (ag_wait_box) instance_destroy();
        ag_wait_box = noone;
    }

    if (ag_scene_job_deferred == 0 && !ag_http_ready)
    {
        // Preserve the request kind before changing ag_state.  The legacy
        // client otherwise loses the order context and reports a generic
        // scene error for a perfectly valid menu drink that resolved as
        // wrong.  Error JSON is diagnostic only; it never changes the
        // original failed-recipe gate in mix_action.
        if (ag_was_order_response && ag_status == 0 && string_length(ag_result) > 0)
        {
            var ag_error_root;
            var ag_error_object;
            ag_error_root = json_decode(ag_result);
            if (ds_exists(ag_error_root, ds_type_map) && ds_map_exists(ag_error_root, "error"))
            {
                ag_error_object = ds_map_find_value(ag_error_root, "error");
                if (ds_exists(ag_error_object, ds_type_map) && ds_map_exists(ag_error_object, "code"))
                    ag_error_code = string(ds_map_find_value(ag_error_object, "code"));
            }
            if (ds_exists(ag_error_root, ds_type_map))
                ds_map_destroy(ag_error_root);
        }
        if (ag_state == 3)
        {
            ag_state = 4;
            if (ag_http_status == 409 && string_length(ag_result) > 0)
            {
                var ag_ack_error_root;
                var ag_ack_error_object;
                var ag_ack_error_code;
                ag_ack_error_root = json_decode(ag_result);
                ag_ack_error_object = noone;
                ag_ack_error_code = "";
                if (ds_exists(ag_ack_error_root, ds_type_map))
                    ag_ack_error_object = ds_map_find_value(ag_ack_error_root, "error");
                if (ds_exists(ag_ack_error_object, ds_type_map) && ds_map_exists(ag_ack_error_object, "code"))
                {
                    ag_ack_error_code = string(ds_map_find_value(ag_ack_error_object, "code"));
                    if (string_count("[", ag_ack_error_code) > 0 || string_count("]", ag_ack_error_code) > 0)
                        ag_ack_error_code = "invalid_error_code";
                    ag_error_message = "O.S.：场景确认被拒绝（" + ag_ack_error_code + "）。";
                }
                else
                    ag_error_message = "O.S.：本地世界服务拒绝了场景确认。";
                if (ds_exists(ag_ack_error_root, ds_type_map))
                    ds_map_destroy(ag_ack_error_root);
            }
            else
                ag_error_message = "O.S.：本地世界服务拒绝了场景确认。";
        }
        else
        {
            ag_state = 4;
            if (ag_http_status == 429)
                ag_error_message = "O.S.：API调用额度已用完，请用更高额度重新启动。";
            else if (ag_was_order_response)
            {
                ag_safe_error_code = ag_error_code;
                if (string_count("[", ag_safe_error_code) > 0 || string_count("]", ag_safe_error_code) > 0)
                    ag_safe_error_code = "invalid_error_code";
                if (ag_safe_error_code == "")
                    ag_error_message = "O.S.：本轮调酒结果无法确认。\n阶段：" + string(ag_last_phase) + "，HTTP " + string(ag_last_http_status) + " / 传输 " + string(ag_last_transport_status) + "\n请求：" + string(ag_request_id) + "\nJob：" + string(ag_last_job_id);
                else
                    ag_error_message = "O.S.：本轮调酒结果无法确认（" + ag_safe_error_code + "）。\n阶段：" + string(ag_last_phase) + "，HTTP " + string(ag_last_http_status) + " / 传输 " + string(ag_last_transport_status) + "\n请求：" + string(ag_request_id) + "\nJob：" + string(ag_last_job_id);
            }
            else if (ag_http_status == 503)
                ag_error_message = "O.S.：剧情生成失败（story_generation_failed）。关闭本段后重新进入即可重试。";
            else if (ag_http_status <= 0)
                ag_error_message = "O.S.：本地服务连接中断，请确认启动命令仍在运行。";
            else
                ag_error_message = "O.S.：本地世界服务拒绝了请求。";
        }
    }
    else if (ag_scene_job_deferred == 0 && ag_state == 3)
    {
        if (ag_order_pending && !ag_order_started)
        {
            resetmixer_2();
            global.slotamount = 1;
            global.orders = ag_order_display_text;
            global.mixhappens = 1;
            global.block_click = 0;
            ag_order_started = 1;
            ag_state = 6;
        }
        else
        {
            if (string_copy(ag_scene_id, 1, 11) == "settlement_")
            {
                var ag_completed_day;
                ag_completed_day = string_copy(ag_scene_id, 12, string_length(ag_scene_id) - 11);
                if (string_copy(ag_completed_day, 1, 4) == "day_")
                    ag_completed_day = string_delete(ag_completed_day, 1, 4);
                if (global.cashcounter > 0)
                {
                    global.jillwallet += global.cashcounter;
                    global.cashcounter = 0;
                }
                // The original new_day transition below is the single
                // authoritative in-session date increment. Do not advance
                // ag_story_day here or the day would be counted twice.
                global.datestring = "O.S. DAY " + string(real(ag_completed_day) + 1);
                if (!instance_exists(new_day))
                    instance_create(x, y, new_day);
                instance_destroy();
            }
            else
            {
                ag_request_sequence += 1;
                ag_request_id = "open_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_request_sequence);
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
                ag_timeout_at = current_time + 120000;
                ag_state = 1;
            }
        }
    }
    else if (ag_scene_job_deferred == 0 && (ag_state == 1 || ag_state == 7))
    {
        var ag_is_order_response;
        var ag_expected_order_id;
        var ag_expected_customer;
        var ag_root;
        var ag_scene;
        var ag_lines;
        var ag_valid;
        var ag_validation_reason;
        var ag_debug_root_size;
        ag_is_order_response = (ag_state == 7);
        ag_expected_order_id = ag_order_id;
        ag_expected_customer = ag_order_customer;
        ag_valid = true;
        ag_validation_reason = "";
        ag_root = json_decode(ag_result);
        ag_debug_root_size = -1;
        if (ds_exists(ag_root, ds_type_map))
            ag_debug_root_size = ds_map_size(ag_root);

        if (!ds_exists(ag_root, ds_type_map))
        {
            ag_valid = false;
            ag_validation_reason = "root_shape";
        }
        if (ag_valid && ds_map_size(ag_root) > 12)
        {
            ag_valid = false;
            ag_validation_reason = "root_size";
        }
        if (ag_valid && (!ds_map_exists(ag_root, "protocol_version") || ds_map_find_value(ag_root, "protocol_version") != 1))
        {
            ag_valid = false;
            ag_validation_reason = "protocol_version";
        }
        if (ag_valid && (!ds_map_exists(ag_root, "request_id") || ds_map_find_value(ag_root, "request_id") != ag_request_id))
        {
            ag_valid = false;
            ag_validation_reason = "request_id";
        }
        if (ag_valid && !ds_map_exists(ag_root, "scene"))
        {
            ag_valid = false;
            ag_validation_reason = "scene_missing";
        }

        if (ag_valid && ag_is_order_response)
        {
            var ag_service_result;
            var ag_service_category;
            var ag_income_delta;
            ag_service_result = ds_map_find_value(ag_root, "result");
            ag_income_delta = ds_map_find_value(ag_root, "income_delta");
            if (!ds_map_exists(ag_root, "income_delta") || ag_income_delta < 0 || ag_income_delta > 10000 || floor(ag_income_delta) != ag_income_delta)
            {
                ag_valid = false;
                ag_validation_reason = "income_delta";
            }
            if (ag_valid && (!ds_map_exists(ag_root, "result") || !ds_exists(ag_service_result, ds_type_map) || ds_map_size(ag_service_result) > 12))
            {
                ag_valid = false;
                ag_validation_reason = "result_size";
            }
            if (ag_valid && (!ds_map_exists(ag_service_result, "order_id") || !ds_map_exists(ag_service_result, "customer_id") || !ds_map_exists(ag_service_result, "category") || !ds_map_exists(ag_service_result, "beverage_id") || !ds_map_exists(ag_service_result, "beverage_name") || !ds_map_exists(ag_service_result, "alcoholic")))
            {
                ag_valid = false;
                ag_validation_reason = "result_fields";
            }
            if (ag_valid && (ds_map_find_value(ag_service_result, "order_id") != ag_expected_order_id || ds_map_find_value(ag_service_result, "customer_id") != ag_expected_customer))
            {
                ag_valid = false;
                ag_validation_reason = "order_identity";
            }
            if (ag_valid)
            {
                ag_service_category = ds_map_find_value(ag_service_result, "category");
                if (ag_service_category != "exact" && ag_service_category != "acceptable" && ag_service_category != "wrong" && ag_service_category != "special")
                {
                    ag_valid = false;
                    ag_validation_reason = "category";
                }
            }
        }

        if (ag_valid)
        {
            ag_scene = ds_map_find_value(ag_root, "scene");
            if (!ds_exists(ag_scene, ds_type_map) || ds_map_size(ag_scene) < 3 || ds_map_size(ag_scene) > 12)
            {
                ag_valid = false;
                ag_validation_reason = "scene_shape";
            }
        }
        if (ag_valid && (!ds_map_exists(ag_scene, "scene_id") || !ds_map_exists(ag_scene, "lines") || !ds_map_exists(ag_scene, "return_to")))
        {
            ag_valid = false;
            ag_validation_reason = "scene_fields";
        }
        if (ag_valid)
        {
            ag_scene_id = ds_map_find_value(ag_scene, "scene_id");
            ag_return_to = ds_map_find_value(ag_scene, "return_to");
            ag_lines = ds_map_find_value(ag_scene, "lines");
            if ((ag_scene_id != "stage_3_connection_test" && string_copy(ag_scene_id, 1, 12) != "world_event_" && string_copy(ag_scene_id, 1, 13) != "order_result_" && string_copy(ag_scene_id, 1, 4) != "day_" && string_copy(ag_scene_id, 1, 8) != "opening_" && string_copy(ag_scene_id, 1, 8) != "waiting_" && string_copy(ag_scene_id, 1, 9) != "doorbell_" && string_copy(ag_scene_id, 1, 8) != "closing_" && string_copy(ag_scene_id, 1, 11) != "settlement_" && string_copy(ag_scene_id, 1, 20) != "music_selection_day_" && string_copy(ag_scene_id, 1, 16) != "pre_opening_day_" && string_copy(ag_scene_id, 1, 10) != "break_day_") || ag_return_to != "bar" || !ds_exists(ag_lines, ds_type_list) || ds_list_size(ag_lines) < 1 || ds_list_size(ag_lines) > 8)
                ag_valid = false;
        }

        ag_order_pending = 0;
        ag_order_id = "";
        ag_order_customer = "";
        ag_order_display_text = "";
        if (ag_valid && ds_map_exists(ag_scene, "order"))
        {
            var ag_order;
            var ag_order_tags;
            ag_order = ds_map_find_value(ag_scene, "order");
            if (!ds_exists(ag_order, ds_type_map) || ds_map_size(ag_order) > 16)
            {
                ag_valid = false;
                ag_validation_reason = "order_shape";
            }
            if (ag_valid && (!ds_map_exists(ag_order, "order_id") || !ds_map_exists(ag_order, "customer_id") || !ds_map_exists(ag_order, "requested_drink_id") || !ds_map_exists(ag_order, "requested_name") || !ds_map_exists(ag_order, "preference_tags") || !ds_map_exists(ag_order, "alcohol_requirement") || !ds_map_exists(ag_order, "display_text")))
            {
                ag_valid = false;
                ag_validation_reason = "order_fields";
            }
            if (ag_valid)
            {
                ag_order_id = ds_map_find_value(ag_order, "order_id");
                ag_order_customer = ds_map_find_value(ag_order, "customer_id");
                ag_order_display_text = ds_map_find_value(ag_order, "display_text");
                ag_order_tags = ds_map_find_value(ag_order, "preference_tags");
                if (string_copy(ag_order_id, 1, 6) != "order_" || string_length(ag_order_display_text) < 1 || string_length(ag_order_display_text) > 160 || string_count("[", ag_order_display_text) > 0 || string_count("]", ag_order_display_text) > 0)
                    ag_valid = false;
                if (ag_order_customer != "dana" && ag_order_customer != "dorothy" && ag_order_customer != "alma" && ag_order_customer != "stella" && ag_order_customer != "sei")
                    ag_valid = false;
                if (!ds_exists(ag_order_tags, ds_type_list) || ds_list_size(ag_order_tags) < 1 || ds_list_size(ag_order_tags) > 4)
                    ag_valid = false;
            }
            if (ag_valid)
                ag_order_pending = 1;
        }

        if (ag_valid)
        {
            ag_line_count = ds_list_size(ag_lines);
            for (var ag_i = 0; ag_i < ag_line_count; ag_i += 1)
            {
                var ag_line;
                var ag_speaker_id;
                var ag_portrait_id;
                var ag_expression_id;
                var ag_line_text;
                ag_line = ds_list_find_value(ag_lines, ag_i);
                if (!ds_exists(ag_line, ds_type_map) || ds_map_size(ag_line) > 8)
                {
                    ag_valid = false;
                    ag_validation_reason = "line_shape";
                    break;
                }
                if (!ds_map_exists(ag_line, "line_id") || !ds_map_exists(ag_line, "speaker_id") || !ds_map_exists(ag_line, "portrait_id") || !ds_map_exists(ag_line, "expression_id") || !ds_map_exists(ag_line, "text"))
                {
                    ag_valid = false;
                    ag_validation_reason = "line_fields";
                    break;
                }
                ag_speaker_id = ds_map_find_value(ag_line, "speaker_id");
                ag_portrait_id = ds_map_find_value(ag_line, "portrait_id");
                ag_expression_id = ds_map_find_value(ag_line, "expression_id");
                ag_line_text = ds_map_find_value(ag_line, "text");
                if (ag_speaker_id != "" && ag_speaker_id != "dana" && ag_speaker_id != "dorothy" && ag_speaker_id != "alma" && ag_speaker_id != "stella" && ag_speaker_id != "sei" && ag_speaker_id != "jill")
                    ag_valid = false;
                if (ag_speaker_id == "")
                {
                    if (ag_portrait_id != "" || ag_expression_id != "neutral")
                        ag_valid = false;
                }
                else if (ag_speaker_id == "jill")
                {
                    if (ag_portrait_id != "")
                        ag_valid = false;
                }
                else
                {
                    if (ag_portrait_id != "sprite_dana" && ag_portrait_id != "sprite_doro" && ag_portrait_id != "sprite_alma" && ag_portrait_id != "sprite_stella" && ag_portrait_id != "sprite_sei")
                        ag_valid = false;
                    if ((ag_speaker_id == "dana" && ag_portrait_id != "sprite_dana") || (ag_speaker_id == "dorothy" && ag_portrait_id != "sprite_doro") || (ag_speaker_id == "alma" && ag_portrait_id != "sprite_alma") || (ag_speaker_id == "stella" && ag_portrait_id != "sprite_stella") || (ag_speaker_id == "sei" && ag_portrait_id != "sprite_sei"))
                        ag_valid = false;
                }
                if (ag_expression_id != "neutral" && ag_expression_id != "happy" && ag_expression_id != "worry" && ag_expression_id != "playful")
                    ag_valid = false;
                // Dynamic text is inserted into a vanilla command-bearing
                // line. Reject brackets so provider output can never inject
                // a textbox command such as [E:] or [SHOW:].
                if (string_length(ag_line_text) < 1 || string_length(ag_line_text) > 72 || string_count("[", ag_line_text) > 0 || string_count("]", ag_line_text) > 0)
                    ag_valid = false;
                if (!ag_valid)
                {
                    ag_validation_reason = "line_values";
                    break;
                }
                ag_speaker[ag_i] = ag_speaker_id;
                ag_portrait[ag_i] = ag_portrait_id;
                ag_text[ag_i] = ag_line_text;
                ag_expression[ag_i] = ag_expression_id;
                ag_display_name[ag_i] = ag_speaker_id;
                if (ag_speaker_id == "dana") ag_display_name[ag_i] = "Dana";
                if (ag_speaker_id == "dorothy") ag_display_name[ag_i] = "Dorothy";
                if (ag_speaker_id == "alma") ag_display_name[ag_i] = "Alma";
                if (ag_speaker_id == "stella") ag_display_name[ag_i] = "Stella";
                if (ag_speaker_id == "sei") ag_display_name[ag_i] = "Sei";
                if (ag_speaker_id == "jill") ag_display_name[ag_i] = "Jill";
                ag_name_color[ag_i] = 15;
                if (ag_speaker_id == "alma") ag_name_color[ag_i] = 14;
                if (ag_speaker_id == "dana") ag_name_color[ag_i] = 15;
                if (ag_speaker_id == "stella") ag_name_color[ag_i] = 16;
                if (ag_speaker_id == "sei") ag_name_color[ag_i] = 17;
                if (ag_speaker_id == "dorothy") ag_name_color[ag_i] = 18;
                if (ag_speaker_id == "jill") ag_name_color[ag_i] = 13;
            }
        }

        if (ds_exists(ag_root, ds_type_map))
            ds_map_destroy(ag_root);

        if (ag_valid)
        {
            if (ag_is_order_response)
            {
                global.cashcounter += ag_income_delta;
                global.barscore += ag_income_delta;
                // The original mixer creates scorepop_obj before the async
                // service response arrives, so its normal score calculation
                // sees shouldpay == 0 and leaves add at zero. Update that
                // short-lived original popup with the authoritative payout;
                // create a late popup only if the first one already expired.
                if (instance_exists(scorepop_obj))
                {
                    with (scorepop_obj) add = ag_income_delta;
                }
                else
                {
                    var ag_scorepop_shouldpay;
                    var ag_scorepop_instance;
                    ag_scorepop_shouldpay = global.shouldpay;
                    global.shouldpay = 0;
                    ag_scorepop_instance = instance_create(370, 85, scorepop_obj);
                    global.shouldpay = ag_scorepop_shouldpay;
                    with (ag_scorepop_instance) add = ag_income_delta;
                }
            }
            // Portrait state belongs to one scene only. Clear it before the
            // first line so a new customer cannot inherit the previous scene's
            // portrait when the first line is Jill or ambient text.
            ag_portrait_speaker = "";
            ag_line_index = 0;
            if (ag_is_order_response)
                ag_order_started = 0;
            ag_state = 2;
            show_debug_message("[OPEN SHIFT] dialogue_ready scene=" + ag_scene_id + " lines=" + string(ag_line_count) + " elapsed_ms=" + string(current_time - ag_wait_started_at));
        }
        else
        {
            ag_state = 4;
            show_debug_message("[OPEN SHIFT] validation_failed reason=" + ag_validation_reason + " request=" + ag_request_id + " order=" + string(ag_is_order_response) + " root_size=" + string(ag_debug_root_size));
            ag_error_message = "O.S.：调酒响应解析失败（" + ag_validation_reason + "）。\n阶段：" + string(ag_last_phase) + "，HTTP " + string(ag_last_http_status) + " / 传输 " + string(ag_last_transport_status) + "\n请求：" + string(ag_request_id) + "\nJob：" + string(ag_last_job_id);
        }
    }
}
