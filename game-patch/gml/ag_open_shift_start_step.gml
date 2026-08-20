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
    global.cur_day = 1001;
    global.cur_client = 1;
    global.cur_stage = 1;
    global.dayphase = "apt";
    global.ag_prefetch_ready = 0;
    global.ag_prefetch_failed = 0;
    global.ag_open_shift_intro_pending = 1;
    global.ag_open_shift_intro_seen = 0;
    room_goto(jill_room);
}
