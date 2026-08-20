if (!instance_exists(config_obj))
{
    if (global.cur_day == 1001 && instance_exists(ag_save_flow_controller) && !ag_save_flow_controller.ag_pair_complete)
    {
        if (instance_exists(data_icon) && !data_icon.chosen)
        {
            clock_icon.alarm[0] = 1;
            augmented_eye_icon.alarm[0] = 1;
            music_icon.alarm[0] = 1;
            dangeru_icon.alarm[0] = 1;
            nanocamo_icon.alarm[0] = 1;
            miki_icon.alarm[0] = 1;
            data_icon.alarm[0] = 10;
            data_icon.chosen = 1;
        }
        else if (!instance_exists(save_home) && !instance_exists(saveloadpage))
        {
            instance_create(x, y, save_home);
        }
    }
    else if (global.cur_day == 1001 && global.ag_prefetch_ready != 1)
    {
        if (instance_exists(ag_preload_controller))
            ag_preload_controller.ag_preload_timeout_at += 0;
    }
    else if (!instance_exists(out_of_apartment))
    {
        instance_create(x, y, out_of_apartment);
    }
}
