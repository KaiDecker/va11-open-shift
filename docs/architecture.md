# OPEN SHIFT 架构

## 运行边界

OPEN SHIFT 由三部分组成：

- GameMaker 补丁：复用原版房间、文本框、调酒器、音乐选择和存档页面；
- 本地 Python bridge：负责认证回环 HTTP、场景队列、调酒结算、存档配对和诊断；
- SQLite 世界库：保存角色状态、事件、剧情游标、每日流程和配对存档。

DeepSeek 只提出受限的行动、世界事件或对白草稿。规则层验证并提交结果，模型不能直接修改
现金、关系、时间、存档或剧情游标。API Key 只通过当前 Windows 用户的 DPAPI 保存。

### 玩家实例

安装器为每个版本创建独立的实例根目录。实例包含补丁后的 `data.win`、可写的选项文件和
`open-shift-links.json`；原版 EXE、DLL 及 `answer`、`config`、`scripts`、`sounds` 等只读资源
通过 Windows 文件链接从 Steam 目录读取。链接创建失败时安装会停止，不会把完整 Steam 目录复制
到实例中。`install.json` 保存实例 ID、版本指纹、数据库和日志路径，卸载时只删除当前实例，
并拒绝以任何方式删除 Steam 原版目录。

玩家发行包不携带 UTMT、原版 `data.win` 或完整 patched `data.win`。开发构建阶段由 UTMT
生成 patched 文件并产出 `patch/data-win.delta`；安装器调用本地 Python bridge 应用该 delta，
先校验正版文件 SHA-256，再校验重建文件 SHA-256，随后才写入隔离实例。

### 角色记忆

`memories` 是角色隔离的长期记忆表。记忆包含 `source_type`（`direct`、`heard`、`rumor`、
`inferred` 或旧库的 `legacy`）、`confidence`、`visibility`、`canonical_key` 和 `archived`。
对话完成时只为实际参与者写入各自视角摘要：亲自发言者记录为亲历，其他在场者记录为听说；
未在场角色不会因为同一事件自动得到记忆。检索按重要度、相关标签、时效、可信度和未解决事项
确定性排序，并受数量和字符预算限制。重复或低价值记忆可以归档，高重要度、承诺和未解决事项不会被归档。

## 原版流程边界

阶段 19 之后，营业流程按原版链路运行：开店前对白和音乐选择由游戏控制；调酒和顾客场景
由 bridge 按场景请求提供动态内容；中场对白完成并收到 ACK 后进入原版休息/四头像存档页；
回到酒吧后再请求下一场景。bridge 不伪造原版房间状态，也不修改 Steam 原版目录。

## 可观测性

启动器和 bridge 写入不含密钥的 `timing.log`。记录请求 ID、阶段、状态码、耗时、fallback、
房间切换和保存配对结果，便于按一次复现定位到 provider、bridge 或 GameMaker 客户端。记忆写入、
检索选择和压缩也会写入同一日志；SQLite 中保留原始事件和角色视角，便于解释角色为何知道某事。
安装后的运行会将 timing 事件写入 `timing.log`；没有配置日志路径的开发库调用默认不刷 stderr，
可用 `OPEN_SHIFT_TIMING_STDERR=1` 显式开启实时控制台输出。
