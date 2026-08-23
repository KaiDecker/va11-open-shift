if (!instance_exists(config_obj))
{
    if (!instance_exists(aa_home))
    {
        instance_destroy();
    }
    if (global.cur_day >= 1001)
    {
        if (place_meeting(x, y, cursor_hitbox) && mouse_check_button_pressed(mb_left) && !instance_exists(aa_art1))
        {
            global.cur_news = 52;
            aa_home.change = 1;
            instance_create(x, y, aa_art1);
            global.jillcomment = global.artcomment1;
        }
    }
    else if (global.cur_day < 19)
    {
        if (place_meeting(x, y, cursor_hitbox))
        {
            if (mouse_check_button_pressed(mb_left))
            {
                if (!instance_exists(aa_art1))
                {
                    if (global.cur_day == 4)
                    {
                        if (global.dondrunk1 == 1 && global.dondrunk3 == 1)
                            global.cur_news = 58;
                        else
                            global.cur_news = (3 * (global.cur_day - 1)) + 1;
                    }
                    else if (global.cur_day == 8)
                    {
                        if (global.almadrunk2 == 1)
                            global.cur_news = 59;
                        else
                            global.cur_news = (3 * (global.cur_day - 1)) + 1;
                    }
                    else
                        global.cur_news = (3 * (global.cur_day - 1)) + 1;
                    aa_home.change = 1;
                    instance_create(x, y, aa_art1);
                    global.jillcomment = global.artcomment1;
                }
            }
        }
    }
}
