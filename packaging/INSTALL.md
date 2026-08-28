# OPEN SHIFT 安装与启动

发布包不包含 VA-11 HALL-A 的可执行文件、`data.win`、原版资源、存档或 API Key。
用户需要拥有正版 Steam Windows 版游戏。开发构建阶段使用 UTMT 生成
`patch/data-win.delta`；玩家发行包不包含 UTMT，安装时只应用该增量补丁，Steam
原版目录永远不会作为写入目标。

1. 解压最新 Windows x64 预览包。包内含 `OpenShift.exe`、带 OPEN SHIFT 图标的
   `OpenShiftSetup.exe`、`OpenShift.ico`、WebView2 Loader 和 `patch/data-win.delta`；
   不需要单独安装 Python 或从网络下载运行时。
2. 双击 `OpenShiftSetup.exe`。这是基于 Win32 + WebView2 的 Windows 图形界面，会自动寻找 Steam 游戏；如果未找到，在图形界面中
   选择包含 `data.win` 的 VA-11 HALL-A 目录。
3. 在图形界面中输入 DeepSeek API Key，然后点击“安装 / 修复”。Key 使用 Windows
   DPAPI 加密，只能由当前 Windows 用户解密，不写入配置、日志、数据库或发布包。
4. 安装完成后点击“准备并启动”，或以后从安装目录运行 `OpenShiftSetup.exe` / `Start-Open-Shift.ps1`。安装器不会创建桌面快捷方式。程序会先
   准备当天剧情，准备完成后自动启动独立实例。实例只保存补丁后的 `data.win`，其余只读游戏资源通过 Windows 链接复用 Steam 目录，
   不再复制完整游戏目录；Steam 原版仍然只读。文件会优先使用同一 NTFS 卷上的 HardLink，跨卷时尝试 SymbolicLink；资源目录使用 Junction。
   如果文件链接权限不足，安装器只会单独复制对应的 EXE/DLL，并在 `open-shift-links.json` 中记录 `copied_file`；目录链接失败仍会明确报错并停止，绝不递归复制完整游戏目录。
   跨卷的 SymbolicLink 可能需要开启 Windows Developer Mode 或以管理员身份运行。
   图形界面中的 DeepSeek 生成模式默认为“快速”；“平衡”只在世界决策中使用
   Thinking，“深度”会让普通对白也使用 Thinking。
5. 在游戏的 `Extra Chapters` 进入 `O.S.`，之后按原版流程从 Jill 房间开始营业。
6. 卸载使用图形界面的“卸载”。确认框会明确提示保留玩家存档，并且始终不改动 Steam 原版。

遇到问题时，请先关闭游戏，保留安装目录中的 `timing.log` 和 `dialogue.log`，并在 Issue 中附上发行包版本、
生成模式（快速/平衡/深度）、复现步骤和脱敏错误信息。不要上传 API Key、数据库、存档或原版 `data.win`。

如果启动器提示缺少 Microsoft Edge WebView2 Runtime，请安装微软的 WebView2
Evergreen Runtime 后重试。玩家发行包不要求安装 .NET 或 Python；Windows 的
WebView2 Runtime 仍由系统提供。

从旧版升级时，启动器会根据 `PACKAGE_MANIFEST.json` 自动使用独立目录
`%LOCALAPPDATA%\OpenShift-<package_version>`（例如 `OpenShift-0.24.0-preview.5`）。如果当前启动器目录已有相同版本的
`install.json`，则继续复用该目录；显式传入 `-InstallDir` 时始终以指定目录为准。每个安装目录都有独立的数据库、日志、配置、API Key 和 OPEN SHIFT 配套存档；
原版 GameMaker 存档仍由正版游戏的原生路径管理；
安装器会比较补丁源指纹，只有当前实例确实属于同一版本时才跳过重建。旧版本目录可以在确认不再需要存档后单独卸载。

安装后的启动入口是安装目录中的 `OpenShiftSetup.exe` 或 `Start-Open-Shift.ps1`，不会创建桌面快捷方式。
发布包仍不包含原版或生成的完整 `data.win`；玩家端不需要 UTMT，只读取正版 `data.win` 并应用包内增量补丁生成独立实例。
