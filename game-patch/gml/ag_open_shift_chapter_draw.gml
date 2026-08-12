draw_self();
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
draw_text(x + 4, y + 1, "OPEN SHIFT");

