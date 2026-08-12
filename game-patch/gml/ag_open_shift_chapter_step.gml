if (!prologuechapter.appear)
{
    instance_destroy();
}
else if (instance_exists(annastart))
{
    y = annastart.y + 14;
    ready = 1;
}
else if (instance_exists(annademostart))
{
    y = annademostart.y + 14;
    ready = 1;
}
else if (instance_exists(annachapter))
{
    if (!ready)
    {
        if (y < annachapter.y + 14)
            y += 2;
        else
            ready = 1;
    }
    else
    {
        y = annachapter.y + 14;
    }
}

if (place_meeting(x, y, cursor_hitbox) && mouse_check_button_pressed(mb_left) && ready)
{
    showbutt = !showbutt;
    if (!instance_exists(ag_open_shift_start))
        instance_create(x, y, ag_open_shift_start);
}

