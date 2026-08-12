ag_hover = mouse_x >= x && mouse_x <= x + ag_width && mouse_y >= y && mouse_y <= y + ag_height;
if (ag_hover)
{
    if (mouse_check_button_pressed(mb_left) && !instance_exists(ag_bridge_controller))
    {
        instance_create(0, 0, ag_bridge_controller);
    }
}

