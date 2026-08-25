// The target game's older GameMaker runtime has no variable_instance_exists.
// Initialize the marker on every textbox so the Step guard can read it safely.
ag_open_shift_wait = global.ag_memory_textbox_wait;
