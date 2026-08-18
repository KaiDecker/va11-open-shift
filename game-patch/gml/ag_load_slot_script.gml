var ag_slot_to_load;
ag_slot_to_load = argument0;
var ag_native_path;
ag_native_path = working_directory + "\saves" + "\Record of Waifu Wars" + "[" + string(ag_slot_to_load) + "]" + ".txt";
if (file_exists(ag_native_path))
{
    var ag_save_file;
    var ag_saved_day;
    ag_save_file = file_text_open_read(ag_native_path);
    file_text_read_string(ag_save_file);
    file_text_readln(ag_save_file);
    file_text_read_real(ag_save_file);
    file_text_readln(ag_save_file);
    ag_saved_day = file_text_read_real(ag_save_file);
    file_text_close(ag_save_file);
    if (ag_saved_day == 1001)
    {
        if (!instance_exists(ag_save_controller))
        {
            global.block_click = 1;
            var ag_restore_controller;
            ag_restore_controller = instance_create(x, y, ag_save_controller);
            ag_restore_controller.ag_operation = "restore";
            ag_restore_controller.ag_slot = ag_slot_to_load;
        }
    }
    else if (!instance_exists(out_to_loading))
    {
        instance_create(x, y, out_to_loading);
        var ag_original_loader;
        ag_original_loader = instance_create(x, y, loader_control);
        ag_original_loader.load_slot = ag_slot_to_load;
    }
}
exit;
