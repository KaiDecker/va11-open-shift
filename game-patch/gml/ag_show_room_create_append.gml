if (global.cur_day == 1001)
{
    if (global.ag_open_shift_intro_seen == 0)
        global.ag_open_shift_intro_pending = 1;
    global.datestring = "O.S. DAY " + string(global.ag_story_day);
    global.shop_casitas = 1;
    global.shop_maneki = 1;
    global.shop_miki = 1;
    global.shop_poster = 1;
    global.shop_carts = 1;
    global.shop_daruma = 1;
    global.shop_y2k = 1;
    global.shop_snatcher = 1;
    global.shop_christmas = 1;
    global.shop_turing = 1;
    global.shop_crt = 1;
    global.shop_fan = 1;
    global.shop_plant = 1;
    global.shop_shoulder = 1;
    global.shop_tea = 1;
    global.shop_beerlot = 1;
    global.shop_banner = 1;
    global.shop_lamp = 1;
    global.tealwall = 1;
    global.creamwall = 1;
    global.redwall = 1;
    global.purplewall = 1;
    global.blackwall = 1;
    global.graywall = 1;
    global.whitewall = 1;
    global.greenwall = 1;
    global.yellowwall = 1;
    global.pinkwall = 1;
    global.stripewall = 1;
    global.cheetahwall = 1;
    global.cirawall = 1;
    global.mikiwall = 1;
    global.juleswall = 1;
    global.radwall = 1;
    global.cheetahtable = 1;
    global.torpedotable = 1;
    global.stripetable = 1;
    global.defaulttable = 1;
    global.streamingsong = 1;
    global.mikisong = 1;
    global.frontiersong = 1;
    global.staffsong = 1;
    global.ironheartsong = 1;
    global.hopesshop = 1;
    global.eltonsshop = 1;
    global.havenshop = 1;
    global.shootersong = 1;
    global.barshop = 1;
    global.endingshop = 1;
    global.friendlyshop = 1;
    global.gotmeshop = 1;
    global.porndl = 1;
    global.lightdl = 1;
    global.housedl = 1;
    global.jillcomment = "JILL: 正在准备今天的营业……";
    if (instance_exists(room_text))
    {
        with (room_text)
        {
            if (global.ag_open_shift_intro_pending == 1)
            {
                deadline = "OPEN SHIFT";
                distraction = "Glitch City 的日子仍在继续。#熟悉的客人也有了新的生活。";
                unlocked = "先看看房间里的新闻，准备完成后再去酒吧上班。";
            }
            else
            {
                deadline = global.datestring;
                distraction = "今天的城市动态会在平板上更新。";
                unlocked = "准备完成后可以去酒吧上班。";
            }
            dismiss = "点击鼠标关闭";
        }
    }
    if (!instance_exists(ag_preload_controller))
    {
        global.ag_prefetch_ready = 0;
        instance_create(x, y, ag_preload_controller);
    }
}
