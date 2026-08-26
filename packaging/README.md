# OPEN SHIFT 玩家发行包

这是 OPEN SHIFT 的 Windows x64 预览版发行包。它包含安装器、启动器、补丁源和运行所需的
脚本，但不包含 VA-11 HALL-A 的可执行文件、`data.win`、原版资源、存档、数据库或 API Key。

## 玩家使用

玩家只需双击 `OpenShiftSetup.exe`。安装器会检查你拥有的 Steam 游戏目录，在另一个目录创建
隔离副本，并在隔离副本中应用补丁。Steam 原版目录不会作为写入目标。

安装完成后，启动器会创建桌面快捷方式。玩家可以在界面中输入自己的 DeepSeek API Key，选择
“快速”“平衡”或“深度”生成模式，然后准备当天剧情并启动游戏。

## 发行包内容

- `OpenShift.exe`：本地 bridge 运行程序；
- `OpenShiftSetup.exe`：WebView2 图形安装器；
- `OpenShift.ico` 和 WebView2 运行库；
- `game-patch/`：补丁源和 GameMaker 注入脚本；
- `packaging/`：安装、启动、卸载和配置脚本；
- `src/open_shift/`：bridge 和规则层源代码；
- `tools/utmt/UndertaleModCli.zip`：安装隔离副本所需的 UTMT CLI；
- 安装、配置和贡献说明。

实机截图和宣传素材不放进玩家发行 ZIP；它们只保存在仓库的素材目录中。

## 生成模式

- **快速**：普通对白不启用 Thinking，响应最快；
- **平衡**：只在世界决策中启用 Thinking；
- **深度**：所有生成请求都启用 Thinking，耗时和 token 消耗最高。

玩家版默认允许本地规则回退。DeepSeek 临时失败时，已经完成规则判断的调酒结果、收入和
剧情游标仍会安全提交。严格的真实 API 验收请使用 `launch-deepseek-acceptance.ps1`。

## 开发者验收

维护者可以使用 PowerShell、Python 3.11+、UTMT CLI 0.9.1.2 和正版 Steam 游戏进行验收。
运行日志写入 `timing.log`，包含请求 ID、状态码、生成模式、耗时和回退事件，不包含 API Key。

阶段 19 已验证原版开店流程、音乐选择、调酒、中场休息存档、回到酒吧和跨日流程。发布前的
最终门槛见仓库根目录的 `RELEASE_CHECKLIST.md`。
