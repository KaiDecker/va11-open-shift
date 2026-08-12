draw_set_alpha(0.85);
draw_set_color(c_black);
draw_rectangle(x, y, x + ag_width, y + ag_height, false);
draw_set_alpha(1);
draw_set_color(c_white);
draw_rectangle(x, y, x + ag_width, y + ag_height, true);
draw_set_font(global.font);
draw_text(x + 10, y + 5, "OPEN SHIFT");

