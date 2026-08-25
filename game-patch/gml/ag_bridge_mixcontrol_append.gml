if (global.cur_day >= 1001 && instance_exists(ag_bridge_controller))
{
    with (ag_bridge_controller)
    {
        if (ag_state == 6 && ag_order_pending && ag_order_started)
        {
            var ag_resolve_headers;
            var ag_resolve_body;
            var ag_drink;
            var ag_preparation;
            ag_preparation = "mixed";
            if (global.failed_a)
                ag_preparation = "blended";

            ag_resolve_headers = ds_map_create();
            ds_map_add(ag_resolve_headers, "Content-Type", "application/json");
            ini_open("open-shift-runtime.ini");
            ds_map_add(ag_resolve_headers, "X-Open-Shift-Token", ini_read_string("bridge", "token", ""));
            ini_close();

            ag_drink = ds_map_create();
            ds_map_add(ag_drink, "adelhyde", global.mod_aa);
            ds_map_add(ag_drink, "bronson_extract", global.mod_ba);
            ds_map_add(ag_drink, "powdered_delta", global.mod_ca);
            ds_map_add(ag_drink, "flanergide", global.mod_da);
            ds_map_add(ag_drink, "karmotrine", global.mod_ea);
            ds_map_add(ag_drink, "ice", global.otr_a);
            ds_map_add(ag_drink, "aged", global.age_a);
            ds_map_add(ag_drink, "preparation", ag_preparation);

            ag_resolve_body = ds_map_create();
            ag_request_sequence += 1;
            ag_request_id = "resolve_" + ag_session_id + "_" + ag_request_scope + "_" + string(ag_request_sequence);
            // Older runtimes may omit the POST response body; derive the same
            // deterministic job id the bridge uses from this request id.
            ag_order_job_id = "order_job_" + ag_request_id;
            ag_last_job_id = ag_order_job_id;
            ds_map_add(ag_resolve_body, "protocol_version", 1);
            ds_map_add(ag_resolve_body, "request_id", ag_request_id);
            ds_map_add(ag_resolve_body, "client_session_id", ag_session_id);
            ds_map_add(ag_resolve_body, "scene_id", ag_scene_id);
            ds_map_add(ag_resolve_body, "order_id", ag_order_id);
            ds_map_add_map(ag_resolve_body, "drink", ag_drink);
            // The legacy "/v1/orders/resolve" endpoint remains available for
            // older clients. Order resolution runs asynchronously because the director may
            // make several provider calls.  Keep the customer textbox alive
            // while the controller polls the short result endpoint.
            ag_http_request = http_request(ag_bridge_url + "/v1/orders/jobs", "POST", ag_resolve_headers, json_encode(ag_resolve_body));
            ds_map_destroy(ag_resolve_body);
            ds_map_destroy(ag_resolve_headers);

            resetmixer_2();
            global.block_click = 1;
            ag_timeout_at = current_time + 120000;
            ag_state = 7;
        }
    }
}
