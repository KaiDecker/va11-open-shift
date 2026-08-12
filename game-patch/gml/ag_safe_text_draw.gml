draw_set_alpha(0.92);
draw_set_color(c_black);
draw_rectangle(16, 320, 624, 464, false);
draw_set_alpha(1);
if (ag_portrait >= 0)
    draw_sprite(ag_portrait, 0, 548, 338);
if (global.language == "jp")
    draw_set_font(jpdialog);
else if (global.language == "ch")
    draw_set_font(dialogfontch);
else if (global.language == "kor")
    draw_set_font(kor_font);
else if (global.language == "rus")
    draw_set_font(rusfont);
else
    draw_set_font(dialogfont);
draw_set_color(c_white);
draw_text(32, 360, ag_speaker);
draw_text_ext(32, 392, ag_text, 20, 500);
draw_text(510, 438, "CLICK >");
