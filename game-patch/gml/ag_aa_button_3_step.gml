if (!instance_exists(config_obj))
{
    if (!instance_exists(aa_home))
    {
        instance_destroy();
    }
    if (global.cur_day == 1001)
    {
        if (place_meeting(x, y, cursor_hitbox) && mouse_check_button_pressed(mb_left) && !instance_exists(aa_art1))
        {
            global.cur_news = 54;
            aa_home.change = 1;
            instance_create(x, y, aa_art1);
            global.jillcomment = global.artcomment3;
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
                    if (global.cur_day == 2)
                    {
                        if (global.dondrunk1 == 1)
                            global.cur_news = 56;
                        else
                            global.cur_news = (3 * (global.cur_day - 1)) + 3;
                    }
                    else if (global.cur_day == 3)
                    {
                        if (global.dondrunk2 == 1)
                            global.cur_news = 60;
                        else
                            global.cur_news = (3 * (global.cur_day - 1)) + 3;
                    }
                    else
                        global.cur_news = (3 * (global.cur_day - 1)) + 3;
                    aa_home.change = 1;
                    instance_create(x, y, aa_art1);
                    global.jillcomment = global.artcomment3;
                }
            }
        }
    }
}
