// Native jukebox hand-off for Open Shift's pre-opening gate.
if (!instance_exists(obj_textbox) && global.jukebox_happens == 1)
{
    if (!instance_exists(jukebox_bg))
    {
        instance_create(x, y, jukebox_bg);
        show_debug_message("[OPEN SHIFT] native_jukebox_open");
    }
}
