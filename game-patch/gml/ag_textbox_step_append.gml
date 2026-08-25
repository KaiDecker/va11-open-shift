// The waiting box uses the original textbox object so its layout and font
// stay native. Its marker is the only special case: a confirm input must not
// make the vanilla Step event close the placeholder before HTTP is ready.
if (ag_open_shift_wait == 1)
{
    textbox_closing = 0;
    textbox_skip_possible = 0;
}
