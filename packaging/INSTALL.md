# Open Shift 安装与启动

发布包不包含 VA-11 HALL-A 的可执行文件、`data.win`、原版资源、存档或 API Key。
用户需要拥有正版 Steam Windows 版游戏；玩家发行包已经包含 UndertaleModTool CLI
0.9.1.2。UTMT 只在安装或修复时把补丁生成到隔离副本，Steam 原版目录永远不会作为
写入目标。

1. 解压发布包。最终玩家包内含 `OpenShift.exe`、带 Open Shift 原创图标的
   `OpenShiftSetup.exe`、`OpenShift.ico` 和 UTMT CLI；
   不需要单独安装 Python 或从网络下载运行时。
2. 双击 `OpenShiftSetup.exe`。程序会自动寻找 Steam 游戏；如果未找到，在图形界面中
   选择包含 `data.win` 的 VA-11 HALL-A 目录。
3. 在图形界面中输入 DeepSeek API Key，然后点击“Install / Repair”。Key 使用 Windows
   DPAPI 加密，只能由当前 Windows 用户解密，不写入配置、日志、数据库或发布包。
4. 安装完成后点击“Prepare and Start”，或以后双击桌面上的 `Open Shift.lnk`。程序会先
   准备当天剧情，准备完成后自动启动隔离副本。
5. 在游戏的 `Extra Chapters` 进入 `O.S.`，之后按原版流程从 Jill 房间开始营业。
6. 卸载使用图形界面的“Uninstall”。默认保留玩家存档，并且始终不改动 Steam 原版。

安装后的启动入口是桌面快捷方式，不需要 PowerShell 命令。发布包仍不包含原版
资源或生成的 `data.win`；UTMT 只在用户本机首次安装时读取正版 `data.win` 并生成
隔离副本。
