if (ag_flow_state == 0 && room == jill_room)
{
    global.block_click = 0;
    global.dayphase = "";
    global.cur_day = 1001;
    global.cur_client = 1;
    global.cur_stage = 1;
    global.ag_story_day_advance_applied = 0;
    global.datestring = "O.S. DAY " + string(global.ag_story_day);
    global.jillcomment = "O.S.：正在准备今天的营业……";
    global.ag_prefetch_ready = 0;
    global.ag_prefetch_day = 0;
    global.ag_preload_notice_day = 0;
    // The controller is persistent and may still contain the previous day's
    // ready response when the bar returns to Jill's room. Restart it so the
    // apartment always asks the bridge for the authoritative current day.
    if (instance_exists(ag_preload_controller))
        with (ag_preload_controller) instance_destroy();
    instance_create(x, y, ag_preload_controller);
    ag_flow_state = 4;
}

if (ag_flow_state == 4 && room == bar)
    instance_destroy();
