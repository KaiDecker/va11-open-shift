// Memory-backed adapter for dynamic Open Shift dialogue.
// The normal path below is the original file-backed implementation. The
// memory path only accepts lines prepared by the bridge from validated fields;
// AI text is never interpreted as a command by this adapter.
if (global.ag_memory_textbox_active == 1)
{
    total_boxes = global.ag_memory_textbox_line_count - 1;
    for (i = 0; i < (total_boxes + 1); i++)
    {
        input_text[i] = global.ag_memory_textbox_lines[i];
    }
    cmd_data_queue = ds_queue_create();
    cmd_pos_queue = ds_queue_create();
    for (i = 0; i < (total_boxes + 1); i++)
    {
        edited_text[i] = textbox_cmd_delete(input_text[i]);
    }
    current_text = 0;
    current_chr = 0;
    current_line = 0;
    textbox_cmd_load(input_text[current_text]);
    next_cmd_pos = ds_queue_head(cmd_pos_queue);
    global.output_text = string_copy(edited_text[current_text], 1, current_chr);
    show_debug_message("[OPEN SHIFT] textbox_memory_load lines=" + string(total_boxes + 1));
    exit;
}

var temp_fname = argument0;
var temp_pointer = argument1;
var temp_line = textbox_find_pointer_line(temp_fname, temp_pointer);
var temp_text = "";
text_queue = ds_queue_create();
var file = file_text_open_read(temp_fname);
repeat (temp_line)
{
    file_text_readln(file);
}
while (!(string_count("[E:", temp_text) > 0 || file_text_eof(file)))
{
    temp_text = file_text_read_string(file);
    ds_queue_enqueue(text_queue, temp_text);
    file_text_readln(file);
}
file_text_close(file);
total_boxes = ds_queue_size(text_queue) - 1;
for (i = 0; i < (total_boxes + 1); i++)
{
    input_text[i] = ds_queue_dequeue(text_queue);
}
ds_queue_destroy(text_queue);
cmd_data_queue = ds_queue_create();
cmd_pos_queue = ds_queue_create();
for (i = 0; i < (total_boxes + 1); i++)
{
    edited_text[i] = textbox_cmd_delete(input_text[i]);
}
textbox_cmd_load(input_text[current_text]);
next_cmd_pos = ds_queue_head(cmd_pos_queue);
global.output_text = string_copy(edited_text[current_text], 1, current_chr);
