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
    "out_to_title",
    "sprite_dana",
    "sprite_doro"
};
string[] newResources = new[]
{
    "ag_open_shift_button",
    "ag_bridge_controller",
    "ag_safe_text"
};

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
    Name = Data.Strings.MakeString("ag_open_shift_button"),
    Visible = true,
    Solid = false,
    Depth = -1000,
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
UndertaleGameObject safeText = new()
{
    Name = Data.Strings.MakeString("ag_safe_text"),
    Visible = true,
    Solid = false,
    Depth = -3000,
    Persistent = false
};
Data.GameObjects.Add(button);
Data.GameObjects.Add(controller);
Data.GameObjects.Add(safeText);

UndertaleModLib.Compiler.CodeImportGroup importGroup = new(Data)
{
    MainThreadAction = MainThreadAction
};

importGroup.QueueReplace(button.EventHandlerFor(EventType.Create, Data), ReadSource("ag_open_shift_button_create.gml"));
importGroup.QueueReplace(button.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_open_shift_button_step.gml"));
importGroup.QueueReplace(button.EventHandlerFor(EventType.Draw, EventSubtypeDraw.Draw, Data), ReadSource("ag_open_shift_button_draw.gml"));
importGroup.QueueReplace(controller.EventHandlerFor(EventType.Create, Data), ReadSource("ag_bridge_controller_create.gml"));
importGroup.QueueReplace(controller.EventHandlerFor(EventType.Step, EventSubtypeStep.Step, Data), ReadSource("ag_bridge_controller_step.gml"));
importGroup.QueueReplace(controller.EventHandlerFor(EventType.Other, (uint)62u, Data), ReadSource("ag_bridge_controller_http.gml"));
importGroup.QueueReplace(safeText.EventHandlerFor(EventType.Create, Data), ReadSource("ag_safe_text_create.gml"));
importGroup.QueueReplace(safeText.EventHandlerFor(EventType.Draw, EventSubtypeDraw.Draw, Data), ReadSource("ag_safe_text_draw.gml"));

UndertaleCode entrypoint = Data.Code.ByName("gml_Object_main_menu_controller_Create_0");
importGroup.QueueAppend(entrypoint, @"
if (!instance_exists(ag_open_shift_button))
    instance_create(254, 318, ag_open_shift_button);");

importGroup.Import();

string[] requiredFunctions = new[]
{
    "http_request", "json_encode", "json_decode", "ds_exists", "ds_map_size",
    "ds_map_exists", "ds_map_find_value", "ds_map_create", "ds_map_add",
    "ds_map_destroy", "ds_list_size", "ds_list_find_value", "ini_open",
    "ini_read_real", "ini_read_string", "ini_close"
};
foreach (string name in requiredFunctions)
{
    if (Data.Functions.ByName(name) is null)
        throw new Exception("GML compiler did not define required function: " + name);
}

string[] expectedCode = new[]
{
    "gml_Object_ag_open_shift_button_Create_0",
    "gml_Object_ag_open_shift_button_Step_0",
    "gml_Object_ag_open_shift_button_Draw_0",
    "gml_Object_ag_bridge_controller_Create_0",
    "gml_Object_ag_bridge_controller_Step_0",
    "gml_Object_ag_bridge_controller_Other_62",
    "gml_Object_ag_safe_text_Create_0",
    "gml_Object_ag_safe_text_Draw_0"
};
foreach (string name in expectedCode)
{
    if (Data.Code.ByName(name) is null)
        throw new Exception("Compiled patch event was missing: " + name);
}

ScriptMessage("Open Shift Stage 3 patch compiled successfully.");
