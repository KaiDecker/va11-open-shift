if (!prologuechapter.appear)
{
    instance_destroy();
}
else if (ag_open_shift_chapter.showbutt)
{
    if (!ready)
    {
        if (y < ag_open_shift_chapter.y + 14)
            y += 2;
        else
            ready = 1;
    }
    else
    {
        y = ag_open_shift_chapter.y + 14;
    }
}
else if (y > ag_open_shift_chapter.y)
{
    y -= 2;
}
else
{
    instance_destroy();
}

if (place_meeting(x, y, cursor_hitbox) && mouse_check_button_pressed(mb_left) && ready)
{
    if (!instance_exists(out_of_apartment))
    {
        global.cur_day = 1001;
        global.cur_client = 1;
        global.cur_stage = 1;
        global.block_click = 0;
        instance_create(x, y, out_of_apartment);
    }
}
