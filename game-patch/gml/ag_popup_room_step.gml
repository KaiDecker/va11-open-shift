if (global.cur_day >= 1001)
{
    if (!ag_open_shift_click_armed)
    {
        if (!mouse_check_button(mb_left))
            ag_open_shift_click_armed = 1;
    }
    else if (mouse_check_button_pressed(mb_left) && !away)
    {
        away = 1;
    }
}
else if (mouse_check_button(mb_left) && !away)
{
    away = 1;
}

if (away)
{
    if (image_yscale > 0)
    {
        image_yscale -= 0.1;
        image_alpha -= 0.12;
    }
    else
    {
        instance_destroy();
    }
}
