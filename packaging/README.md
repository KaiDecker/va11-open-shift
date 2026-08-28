# OPEN SHIFT 玩家发行包

这是 OPEN SHIFT 的 Windows x64 预览版发行包。它包含安装器、启动器、补丁源和运行所需的
脚本，但不包含 VA-11 HALL-A 的可执行文件、`data.win`、原版资源、存档、数据库或 API Key。

## 玩家使用

玩家只需双击 `OpenShiftSetup.exe`。安装器会检查你拥有的 Steam 游戏目录，在另一个目录创建
独立实例，并在实例中应用补丁。实例只保存补丁后的 `data.win`，其余只读资源通过 Windows 链接复用 Steam 目录，
因此不会复制完整游戏目录。文件优先使用 HardLink，目录使用 Junction，跨卷文件链接会尝试 SymbolicLink。
若文件链接权限不足，只会逐个复制对应的 EXE/DLL，并记录 `copied_file`；目录 Junction 创建失败会停止并显示原因，不会静默回退为完整复制。
跨卷 SymbolicLink 可能需要 Windows Developer Mode 或管理员权限。
Steam 原版目录不会作为写入目标。

安装完成后，玩家从对应版本的安装目录启动 `OpenShiftSetup.exe`，也可以直接运行 `Start-Open-Shift.ps1`。默认安装目录是
`%LOCALAPPDATA%\OpenShift-<package_version>`；当前目录已有同版本 `install.json` 时会复用，显式 `-InstallDir` 不受影响。
安装器不会创建桌面快捷方式。玩家可以在界面中输入自己的 DeepSeek API Key，选择
“快速”“平衡”或“深度”生成模式，然后准备当天剧情并启动游戏。

## 发行包内容

- `OpenShift.exe`：本地 bridge 运行程序；
- `OpenShiftSetup.exe`：WebView2 图形安装器；
- `OpenShift.ico` 和 WebView2 运行库；
- `game-patch/`：补丁源和 GameMaker 注入脚本；
- `packaging/`：安装、启动、卸载和配置脚本；
- `src/open_shift/`：bridge 和规则层源代码；
- `patch/data-win.delta`：开发构建期由 UTMT 生成的、供玩家端应用的增量补丁；
- 安装、配置和贡献说明。

实机截图和宣传素材不放进玩家发行 ZIP；它们只保存在仓库的素材目录中。

## 生成模式

- **快速**：普通对白不启用 Thinking，响应最快；
- **平衡**：只在世界决策中启用 Thinking；
- **深度**：所有生成请求都启用 Thinking，耗时和 token 消耗最高。

玩家版默认允许本地规则回退。DeepSeek 临时失败时，已经完成规则判断的调酒结果、收入和
剧情游标仍会安全提交。严格的真实 API 验收请使用 `launch-deepseek-acceptance.ps1`。

## 开发者验收

维护者可以使用 PowerShell、Python 3.11+、UTMT CLI 0.9.1.2 和正版 Steam 游戏生成发行包；玩家端不需要 UTMT。
运行日志写入 `timing.log`，包含请求 ID、状态码、生成模式、耗时、回退事件和记忆诊断，不包含 API Key、
提示词或模型完整响应。遇到问题时可同时提交脱敏后的 `dialogue.log`，其中只包含实际显示的对白。

阶段 19 已验证原版开店流程、音乐选择、调酒、中场休息存档、回到酒吧和跨日流程。阶段 23 增加了
轻量实例、`open-shift-links.json` 链接清单和每版本独立数据目录。发布前的
最终门槛见仓库根目录的 `RELEASE_CHECKLIST.md`。
