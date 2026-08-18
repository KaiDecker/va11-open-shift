if (ag_flow_state == 0 && room == jill_room)
{
    global.block_click = 0;
    ag_flow_state = 1;
}

if (ag_flow_state == 1 && ag_pair_complete && !instance_exists(ag_save_controller))
{
    if (instance_exists(saveloadpage))
        saveloadpage.out = 1;
    if (instance_exists(save_home))
        save_home.out = 1;
    ag_flow_state = 2;
}

if (ag_flow_state == 2 && !instance_exists(saveloadpage) && !instance_exists(save_home))
{
    global.block_click = 0;
    global.dayphase = "";
    global.cur_day = 1001;
    global.cur_client = 1;
    global.cur_stage = 1;
    ag_flow_state = 4;
}

if ((ag_flow_state == 3 || ag_flow_state == 4) && room == bar)
    instance_destroy();
