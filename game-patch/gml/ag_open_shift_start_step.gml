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
    if (!instance_exists(ag_bridge_controller))
    {
        prologuechapter.appear = 0;
        global.block_click = 1;
        instance_create(0, 0, ag_bridge_controller);
    }
}

