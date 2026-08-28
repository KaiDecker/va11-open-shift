if (!instance_exists(config_obj) && !instance_exists(ag_save_controller))
{
    if (global.cur_data == "save")
    {
        save_script(ag_save_slot);
        // Pair every Open Shift save, including DAY2 and later.  The vanilla
        // save still owns the money and room state; the paired snapshot owns
        // the external world day and agent memory.
        if (global.cur_day >= 1001)
        {
            global.block_click = 1;
            var ag_pair_controller;
            ag_pair_controller = instance_create(x, y, ag_save_controller);
            ag_pair_controller.ag_operation = "pair";
            ag_pair_controller.ag_slot = ag_save_slot;
        }
    }
    else
    {
        load_slot_script(ag_save_slot);
    }
}
