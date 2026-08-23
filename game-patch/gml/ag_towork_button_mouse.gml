if (!instance_exists(config_obj))
{
    var ag_story_ready = global.cur_day >= 1001 && global.ag_prefetch_ready == 1 && global.ag_prefetch_day == global.ag_story_day;
    var ag_intro_blocking = global.ag_open_shift_intro_pending == 1 || global.ag_open_shift_intro_seen == 0;
    var ag_debug_preload_state = -1;
    if (instance_exists(ag_preload_controller))
        ag_debug_preload_state = ag_preload_controller.ag_preload_state;
    show_debug_message("[OPEN SHIFT] work_click ready=" + string(ag_story_ready) + " prefetch_day=" + string(global.ag_prefetch_day) + " story_day=" + string(global.ag_story_day) + " preload_state=" + string(ag_debug_preload_state));
    if (global.cur_day >= 1001 && (!ag_story_ready || ag_intro_blocking))
    {
        if (instance_exists(ag_preload_controller))
            ag_preload_controller.ag_preload_timeout_at += 0;
        if (ag_intro_blocking)
            show_debug_message("[OPEN SHIFT] work_click rejected reason=intro");
        else
            show_debug_message("[OPEN SHIFT] work_click rejected reason=preload_not_ready");
    }
    else if (!instance_exists(out_of_apartment))
    {
        // A ready-day popup may still be fading after the player clicked it.
        // Treat the work button as the authoritative interaction: dismiss
        // that visual layer and enter the bar in the same click.
        if (instance_exists(popup_room))
            with (popup_room) away = 1;
        global.ag_story_day_advance_applied = 0;
        show_debug_message("[OPEN SHIFT] work_click accepted day=" + string(global.ag_story_day));
        instance_create(x, y, out_of_apartment);
    }
}
