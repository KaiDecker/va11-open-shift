using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Underanalyzer.Decompiler;
using UndertaleModLib.Models;
using UndertaleModLib.Util;

EnsureDataLoaded();

string output = Path.Combine(Path.GetDirectoryName(FilePath), "open-shift-analysis");
Directory.CreateDirectory(output);

string[] requested = new[]
{
    "gml_Object_extrachapters_Create_0",
    "gml_Object_extrachapters_Step_0",
    "gml_Object_out_to_title_Create_0",
    "gml_Object_out_to_title_Step_0",
    "gml_Object_out_to_title_Draw_0",
    "gml_Object_title_to_room_Step_0",
    "gml_Object_main_menu_controller_Create_0",
    "gml_Object_main_menu_controller_Step_0",
    "gml_Object_main_menu_controller_Draw_0",
    "gml_Object_mainng_button_Create_0",
    "gml_Object_mainng_button_Step_0",
    "gml_Object_mainload_button_Create_0",
    "gml_Object_mainload_button_Step_0",
    "gml_Object_mainsettings_button_Create_0",
    "gml_Object_mainsettings_button_Step_0",
    "gml_Object_mainexit_button_Create_0",
    "gml_Object_mainexit_button_Step_0"
};

GlobalDecompileContext globalContext = new(Data);
foreach (string name in requested)
{
    UndertaleCode code = Data.Code.ByName(name);
    if (code is null)
        continue;
    string source = new DecompileContext(
        globalContext,
        code,
        Data.ToolInfo.DecompilerSettings
    ).DecompileToString();
    File.WriteAllText(Path.Combine(output, name + ".gml"), source);
}

string[] functionNames = Data.Functions
    .Where(function => function?.Name?.Content is not null)
    .Select(function => function.Name.Content)
    .Where(name =>
        name.Contains("http", StringComparison.OrdinalIgnoreCase) ||
        name.Contains("json", StringComparison.OrdinalIgnoreCase) ||
        name.Contains("ds_map", StringComparison.OrdinalIgnoreCase) ||
        name.Contains("buffer", StringComparison.OrdinalIgnoreCase))
    .Distinct()
    .OrderBy(name => name)
    .ToArray();
File.WriteAllLines(Path.Combine(output, "relevant-functions.txt"), functionNames);

string[] roomNames = Data.Rooms
    .Where(room => room?.Name?.Content is not null)
    .Select(room => room.Name.Content)
    .OrderBy(name => name)
    .ToArray();
File.WriteAllLines(Path.Combine(output, "rooms.txt"), roomNames);

ScriptMessage("Open Shift entrypoint analysis exported.");
