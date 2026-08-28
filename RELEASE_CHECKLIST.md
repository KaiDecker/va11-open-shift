# OPEN SHIFT 发布清单

这份清单用于维护者在创建 GitHub Release 前逐项验收。历史 RC 故障不记录在这里；复现问题
应写入 Issue，并附版本、日志和最小复现步骤。

## 版本与输入

- [ ] 工作区干净，发布 commit、版本号和目标平台已确定。
- [ ] 本次公开预览版本统一为 `v0.24.0-preview`，包名、安装目录和界面版本显示一致。
- [ ] `game-patch/manifest.json` 中的 Steam Windows 哈希与受支持版本一致。
- [ ] Steam 原版 `data.win` 和 EXE 哈希在构建前后不变。
- [ ] 构建输入不包含 API Key、数据库、存档、原版资源或生成的 `data.win`。

## 自动验证

- [ ] `python -m unittest discover -s tests` 全部通过。
- [ ] 补丁从受支持原版构建，`verify-patch-output` 通过。
- [ ] UTMT 往返写入两次得到相同哈希。
- [ ] `git diff --check` 通过，发行包清单无敏感文件。

## 安装与游戏验收

- [ ] 全新 Windows x64 环境可用 `OpenShiftSetup.exe` 安装隔离副本。
- [ ] WebView2 缺失时显示可操作的中文诊断。
- [ ] 启动器启动本地服务后，GameMaker 可从 `%LOCALAPPDATA%\VA_11_Hall_A\open-shift-runtime.ini` 读取当前端口、令牌和会话信息；世界状态不会因配置路径错误而一直停留在准备中。
- [ ] 完成至少两天：开店前对白、原版音乐选择、顾客调酒、中场休息/四头像存档、回到酒吧、
      下一天和 O.S. DAY N 均可操作。
- [ ] 重启后可从配对存档恢复；provider 失败有可重试提示或本地安全回退。
- [ ] 至少在一台真实 Windows x64 机器上完成全新安装、保存 API Key、准备世界状态、启动游戏和关闭重启验证。
- [ ] 卸载可恢复备份并默认保留玩家存档。

## 发布包与安全

- [ ] 包含 `OpenShift.exe`、`OpenShiftSetup.exe`、图标、运行时和安装脚本，不包含 `data.win`、
      游戏 EXE、数据库、`reference-local` 或 API Key。
- [ ] 轻量包审计通过：不包含 UTMT、`UndertaleModCli.exe`、完整 `data.win`、Python/GML 源码、
      验收截图或旧版构建产物；只保留玩家安装和运行所需文件。
- [ ] 安装、备份、配置、启动、读档、卸载和故障排查文档与当前包一致。
- [ ] GitHub Release 说明兼容 Steam 哈希、Windows x64 范围、安装步骤和 SHA-256。
- [ ] README、贡献指南和路线图已同步当前版本，并给出 Issue/PR 入口。

## v0.24.0-preview 专项记录

- [x] 已确认 preview.1 的运行配置路径问题不再复现：本地服务已启动时，游戏可以进入 `ready` 状态。
- [ ] 已记录 preview 玩家 ZIP 的 SHA-256；创建 Release 后再与远端附件核对：
      `3b90fd6eb1111461423cc1ccc8432c96c6affd380a1310df2e63693b944fca3e`。
- [ ] 已确认旧版 `v0.19.0-rc.23` 仅作为历史版本，不作为公开下载入口或验收依据。
