if (!instance_exists(config_obj))
{
    if (global.cur_day == 1001 && global.ag_prefetch_ready != 1)
    {
        if (instance_exists(ag_preload_controller))
            ag_preload_controller.ag_preload_timeout_at += 0;
    }
    else if (!instance_exists(out_of_apartment))
    {
        global.ag_story_day_advance_applied = 0;
        instance_create(x, y, out_of_apartment);
    }
}
