using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using UndertaleModLib.Models;
using UndertaleModLib.Util;

EnsureDataLoaded();

const string ExpectedSha256 = "f14c4443838179f633f362c6fa20ca849d479c555eb315a507b4165ffa940991";
string[] requiredResources = new[]
{
    "extrachapters",
    "gml_Object_extrachapters_Create_0",
    "gml_Object_extrachapters_Step_0",
    "main_menu_controller",
    "gml_Object_main_menu_controller_Create_0",
    "prologuechapter",
    "gml_Object_prologuechapter_Step_0",
    "blue_chapter",
    "yellow_chapter",
    "cursor_hitbox",
    "out_of_apartment",
    "towork_load",
    "bar",
    "extrachapter_text",
    "gml_Object_extrachapter_text_Draw_0",
    "dialog_control",
    "gml_Object_dialog_control_Create_0",
    "recipebook_bg",
    "order_text",
    "obj_textbox",
    "gml_Script_reset_lips",
    "gml_Script_resetmixer_2",
    "gml_Script_mixcontrol",
    "gml_Script_load_slot_script",
    "save_home",
    "saveloadpage",
    "jill_room",
    "tablet_control",
    "gml_Object_tablet_control_Create_0",
    "gml_Object_show_room_Create_0",
    "room_text",
    "gml_Object_room_text_Draw_0",
    "popup_room",
    "roompopup_spr",
    "gml_Object_popup_room_Create_0",
    "gml_Object_popup_room_Step_0",
    "gml_Object_var_controller_Create_0",
    "aa_home",
    "aa_art1",
    "aa_button_1",
    "aa_button_2",
    "aa_button_3",
    "out_of_loading",
    "gml_Object_towork_button_Mouse_4",
    "data_icon",
    "clock_icon",
    "augmented_eye_icon",
    "music_icon",
    "dangeru_icon",
    "nanocamo_icon",
    "miki_icon",
    "sprite_dana",
    "sprite_doro",
    "sprite_alma",
    "sprite_stella",
    "sprite_sei"
};
string[] newResources = new[]
{
    "ag_open_shift_chapter",
    "ag_open_shift_start",
    "ag_bridge_controller",
    "ag_save_controller",
    "ag_save_flow_controller",
    "ag_tablet_controller",
    "ag_preload_controller"
};

for (int slot = 1; slot <= 24; slot++)
{
    requiredResources = requiredResources.Concat(new[]
    {
        $"save{slot}",
        $"gml_Object_save{slot}_Create_0",
        $"gml_Object_save{slot}_Mouse_4"
    }).ToArray();
}

string inputHash;
using (FileStream stream = File.OpenRead(FilePath))
    inputHash = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
if (inputHash != ExpectedSha256)
    throw new Exception("Unsupported data.win baseline. Refusing to patch: " + inputHash);

foreach (string name in requiredResources)
{
    bool found = Data.GameObjects.ByName(name) is not null ||
                 Data.Code.ByName(name) is not null ||
                 Data.Sprites.ByName(name) is not null ||
                 Data.Rooms.ByName(name) is not null;
    if (!found)
        throw new Exception("Required resource was missing: " + name);
}
foreach (string name in newResources)
{
    if (Data.GameObjects.ByName(name) is not null || Data.Code.ByName(name) is not null)
        throw new Exception("Patch resource already exists: " + name);
}

string sourceDirectory = Path.Combine(Path.GetDirectoryName(ScriptPath), "gml");
string ReadSource(string filename)
{
    string path = Path.Combine(sourceDirectory, filename);
    if (!File.Exists(path))
        throw new Exception("Patch source was missing: " + filename);
    string source = File.ReadAllText(path);
    string normalized = source.ToLowerInvariant();
    string[] banned = new[]
    {
        "execute_string", "shell_execute", "file_delete", "directory_destroy",
        "environment_get_variable", "network_create_server"
    };
    string capability = banned.FirstOrDefault(normalized.Contains);
    if (capability is not null)
        throw new Exception("Patch source used a banned capability: " + capability);
    return source;
}

UndertaleGameObject button = new()
{
    Name = Data.Strings.MakeString("ag_open_shift_chapter"),
    Sprite = Data.Sprites.ByName("blue_chapter"),
    Visible = true,
    Solid = false,
    Depth = -189,
    Persistent = false
};
UndertaleGameObject start = new()
{
    Name = Data.Strings.MakeString("ag_open_shift_start"),
    Sprite = Data.Sprites.ByName("yellow_chapter"),
    Visible = true,
    Solid = false,
    Depth = -180,
    Persistent = false
};
UndertaleGameObject controller = new()
{
    Name = Data.Strings.MakeString("ag_bridge_controller"),
    Visible = false,
    Solid = false,
    Depth = -2000,
    Persistent = false
};
UndertaleGameObject saveController = new()
{
    Name = Data.Strings.MakeString("ag_save_controller"),
    Visible = false,
    Solid = false,
    Depth = -2001,
    Persistent = true
};
UndertaleGameObject saveFlowController = new()
{
    Name = Data.Strings.MakeString("ag_save_flow_controller"),
    Visible = false,
    Solid = false,
    Depth = -2002,
    Persistent = true
};
UndertaleGameObject tabletController = new()
{
    Name = Data.Strings.MakeString("ag_tablet_controller"),
    Visible = false,
    Solid = false,
    Depth = -2003,
    Persistent = false
};
UndertaleGameObject preloadController = new()
{
    Name = Data.Strings.MakeString("ag_preload_controller"),
    Visible = false,
    Solid = false,
    Depth = -2004,
    Persistent = true
};
Data.GameObjects.Add(button);
Data.GameObjects.Add(start);
Data.GameObjects.Add(controller);
Data.GameObjects.Add(saveController);
Data.GameObjects.Add(saveFlowController);
Data.GameObjects.Add(tabletController);
Data.GameObjects.Add(preloadController);

UndertaleModLib.Compiler.CodeImportGroup importGroup = new(Data)
{
    MainThreadAction = MainThreadAction
};

importGroup.QueueReplace(button.EventHandlerFor(EventType.Create, Data), ReadSource("ag_open_shift_chapter_create.gml"));
importGroup.QueueReplace(button.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_open_shift_chapter_step.gml"));
importGroup.QueueReplace(start.EventHandlerFor(EventType.Create, Data), ReadSource("ag_open_shift_start_create.gml"));
importGroup.QueueReplace(start.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_open_shift_start_step.gml"));
importGroup.QueueReplace(controller.EventHandlerFor(EventType.Create, Data), ReadSource("ag_bridge_controller_create.gml"));
importGroup.QueueReplace(controller.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_bridge_controller_step.gml"));
importGroup.QueueReplace(controller.EventHandlerFor(EventType.Other, (uint)62u, Data), ReadSource("ag_bridge_controller_http.gml"));
importGroup.QueueReplace(saveController.EventHandlerFor(EventType.Create, Data), ReadSource("ag_save_controller_create.gml"));
importGroup.QueueReplace(saveController.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_save_controller_step.gml"));
importGroup.QueueReplace(saveController.EventHandlerFor(EventType.Other, (uint)62u, Data), ReadSource("ag_save_controller_http.gml"));
importGroup.QueueReplace(saveFlowController.EventHandlerFor(EventType.Create, Data), ReadSource("ag_save_flow_controller_create.gml"));
importGroup.QueueReplace(saveFlowController.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_save_flow_controller_step.gml"));
importGroup.QueueReplace(tabletController.EventHandlerFor(EventType.Create, Data), ReadSource("ag_tablet_controller_create.gml"));
importGroup.QueueReplace(tabletController.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_tablet_controller_step.gml"));
importGroup.QueueReplace(preloadController.EventHandlerFor(EventType.Create, Data), ReadSource("ag_preload_controller_create.gml"));
importGroup.QueueReplace(preloadController.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_preload_controller_step.gml"));
importGroup.QueueReplace(preloadController.EventHandlerFor(EventType.Other, (uint)62u, Data), ReadSource("ag_preload_controller_http.gml"));
importGroup.QueueReplace(preloadController.EventHandlerFor(EventType.Draw, Data), ReadSource("ag_preload_controller_draw.gml"));

UndertaleCode showRoomCreate = Data.Code.ByName("gml_Object_show_room_Create_0");
importGroup.QueueAppend(showRoomCreate, ReadSource("ag_show_room_create_append.gml"));

UndertaleCode popupRoomCreate = Data.Code.ByName("gml_Object_popup_room_Create_0");
UndertaleCode popupRoomStep = Data.Code.ByName("gml_Object_popup_room_Step_0");
importGroup.QueueAppend(popupRoomCreate, ReadSource("ag_popup_room_create_append.gml"));
importGroup.QueueReplace(popupRoomStep, ReadSource("ag_popup_room_step.gml"));

UndertaleCode variableControllerCreate = Data.Code.ByName("gml_Object_var_controller_Create_0");
importGroup.QueueAppend(variableControllerCreate, ReadSource("ag_var_controller_create_append.gml"));

UndertaleCode tabletCreate = Data.Code.ByName("gml_Object_tablet_control_Create_0");
importGroup.QueueAppend(tabletCreate, @"
if (global.cur_day == 1001 && !instance_exists(ag_tablet_controller))
    instance_create(x, y, ag_tablet_controller);");

for (int slot = 1; slot <= 24; slot++)
{
    UndertaleCode saveCreate = Data.Code.ByName($"gml_Object_save{slot}_Create_0")
        ?? throw new Exception($"Save slot Create event was missing: {slot}");
    UndertaleCode saveMouse = Data.Code.ByName($"gml_Object_save{slot}_Mouse_4")
        ?? throw new Exception($"Save slot Mouse event was missing: {slot}");
    importGroup.QueueAppend(saveCreate, $"\nag_save_slot = {slot};");
    importGroup.QueueReplace(saveMouse, ReadSource("ag_save_slot_mouse.gml"));
}

UndertaleCode loadSlotScript = Data.Code.ByName("gml_Script_load_slot_script");
importGroup.QueueReplace(loadSlotScript, ReadSource("ag_load_slot_script.gml"));

UndertaleCode toWorkMouse = Data.Code.ByName("gml_Object_towork_button_Mouse_4");
importGroup.QueueReplace(toWorkMouse, ReadSource("ag_towork_button_mouse.gml"));

UndertaleCode aaButton1Step = Data.Code.ByName("gml_Object_aa_button_1_Step_0");
UndertaleCode aaButton2Step = Data.Code.ByName("gml_Object_aa_button_2_Step_0");
UndertaleCode aaButton3Step = Data.Code.ByName("gml_Object_aa_button_3_Step_0");
importGroup.QueueReplace(aaButton1Step, ReadSource("ag_aa_button_1_step.gml"));
importGroup.QueueReplace(aaButton2Step, ReadSource("ag_aa_button_2_step.gml"));
importGroup.QueueReplace(aaButton3Step, ReadSource("ag_aa_button_3_step.gml"));

UndertaleCode entrypoint = Data.Code.ByName("gml_Object_prologuechapter_Step_0");
importGroup.QueueAppend(entrypoint, @"
if (appear && image_xscale >= 1 && instance_exists(annachapter) && !instance_exists(ag_open_shift_chapter))
    instance_create(x, y, ag_open_shift_chapter);");

UndertaleCode chapterTextDraw = Data.Code.ByName("gml_Object_extrachapter_text_Draw_0");
importGroup.QueueAppend(chapterTextDraw, @"
if (instance_exists(ag_open_shift_chapter) && ag_open_shift_chapter.ready == 1)
{
    if (global.language == ""jp"") draw_set_font(jpdialog2);
    else if (global.language == ""ch"") draw_set_font(ch_small);
    else if (global.language == ""kor"") draw_set_font(kor_fontsm);
    else if (global.language == ""rus"") draw_set_font(rusfontsm);
    else draw_set_font(dialogfont2);
    draw_set_color(#237CF4);
    draw_text(ag_open_shift_chapter.x + 2, (ag_open_shift_chapter.y - 1) + t_offset, ""O.S."");
}
if (instance_exists(ag_open_shift_start) && ag_open_shift_start.ready == 1)
{
    if (global.language == ""jp"") draw_set_font(jpdialog2);
    else if (global.language == ""ch"") draw_set_font(ch_small);
    else if (global.language == ""kor"") draw_set_font(kor_fontsm);
    else if (global.language == ""rus"") draw_set_font(rusfontsm);
    else draw_set_font(dialogfont2);
    draw_set_color(#F4B323);
    draw_text(ag_open_shift_start.x + 2, (ag_open_shift_start.y - 1) + t_offset, ""START"");
}");

UndertaleCode dialogCreate = Data.Code.ByName("gml_Object_dialog_control_Create_0");
importGroup.QueueAppend(dialogCreate, @"
if (global.cur_day == 1001 && !instance_exists(ag_bridge_controller))
{
    global.block_click = 1;
    instance_create(x, y, ag_bridge_controller);
}");

UndertaleCode mixControl = Data.Code.ByName("gml_Script_mixcontrol");
importGroup.QueueAppend(mixControl, ReadSource("ag_bridge_mixcontrol_append.gml"));

importGroup.Import();

string[] requiredFunctions = new[]
{
    "http_request", "json_encode", "json_decode", "ds_exists", "ds_map_size",
    "ds_map_exists", "ds_map_find_value", "ds_map_create", "ds_map_add",
    "ds_map_destroy", "ds_map_add_map", "ds_list_size", "ds_list_find_value", "ini_open",
    "ini_read_real", "ini_read_string", "ini_close"
};
foreach (string name in requiredFunctions)
{
    if (Data.Functions.ByName(name) is null)
        throw new Exception("GML compiler did not define required function: " + name);
}

string[] expectedCode = new[]
{
    "gml_Object_ag_open_shift_chapter_Create_0",
    "gml_Object_ag_open_shift_chapter_Step_0",
    "gml_Object_ag_open_shift_start_Create_0",
    "gml_Object_ag_open_shift_start_Step_0",
    "gml_Object_ag_bridge_controller_Create_0",
    "gml_Object_ag_bridge_controller_Step_0",
    "gml_Object_ag_bridge_controller_Other_62",
    "gml_Object_ag_save_controller_Create_0",
    "gml_Object_ag_save_controller_Step_0",
    "gml_Object_ag_save_controller_Other_62",
    "gml_Object_ag_save_flow_controller_Create_0",
    "gml_Object_ag_save_flow_controller_Step_0"
    ,"gml_Object_ag_tablet_controller_Create_0"
    ,"gml_Object_ag_tablet_controller_Step_0"
    ,"gml_Object_ag_preload_controller_Create_0"
    ,"gml_Object_ag_preload_controller_Step_0"
    ,"gml_Object_ag_preload_controller_Other_62"
    ,"gml_Object_ag_preload_controller_Draw_0"
};
foreach (string name in expectedCode)
{
    if (Data.Code.ByName(name) is null)
        throw new Exception("Compiled patch event was missing: " + name);
}

ScriptMessage("Open Shift Stage 10 MVP patch compiled successfully.");
