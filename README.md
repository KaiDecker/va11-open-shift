# Open Shift Simulator

这是 VA-11 HALL-A 多 Agent 永续世界 Mod。当前已完成 BYOK 决策闭环、多 Agent 社会模拟，以及阶段 3 的 GameMaker 本地桥接和可复现补丁源码。仓库不会包含或分发游戏资源。

当前能力：

- SQLite 持久化世界状态、事件、记忆、关系、目标和待处理事件
- 事件驱动的世界时间，不按每秒轮询所有角色
- 模型供应商接口、完全确定性的 `MockProvider` 与 BYOK HTTP Provider
- 已验证的兼容 Chat Completions 协议，以及可选的 Responses 风格隔离适配器
- 严格 JSON 行动校验、单次探针、调用预算和安全故障兜底
- 白名单行动与规则验证，Provider 不能直接修改数据库
- Dana、Dorothy、Alma、Stella、Sei 五个持久 Agent
- Agent 私有、相关且有固定上下文预算的确定性记忆检索
- 消息、邀请、承诺、双向关系后果和可持续事件弧
- 目标完成后创建后续目标，世界不依赖玩家触发即可继续演化
- 30/100 天无人值守模拟、断点续跑和确定性回放测试
- 仅监听本机回环地址、带令牌和幂等语义的 GameMaker HTTP 桥接协议
- 原版 `data.win` 只读盘点、哈希基线和按资源名验证的补丁合同
- UndertaleModTool 0.9.1.2 补丁脚本、Extra Chapters 入口和安全三句连接场景
- 安全 launcher、动态 Agent 世界场景、服务重启幂等和玩家观看结果回写

## 运行

无需安装第三方依赖。进入本目录后执行：

```powershell
$env:PYTHONPATH = "src"
python -m open_shift simulate --db work/demo.sqlite3 --days 30 --fresh
```

机器可读报告：

```powershell
$env:PYTHONPATH = "src"
python -m open_shift simulate --db work/demo.sqlite3 --days 30 --fresh --json
```

测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 设计边界

- Provider 只返回结构化行动提案。
- `RuleEngine` 检查地点、金钱、对象和权限后才提交事件。
- 过去的生成结果会进入事件日志；读档不会重新生成过去。
- BYOK Key 只从用户指定的环境变量读取，不写入数据库或普通配置。
- Provider 的网络层可注入；自动测试不会发出真实请求。
- Provider 只能看到当前行动者检索出的记忆、邀请、承诺和事件弧，不能读取其他角色的私有记忆。
- 远程端点必须使用 HTTPS，明文 HTTP 只允许本机回环地址。
- 当前已使用 DeepSeek 的兼容 Chat Completions 端点完成真实单次探针；其他供应商和协议需要各自单独验证。

## BYOK 单次探针

探针只请求一次决策，不创建或修改世界数据库。不要把 API Key 放在命令参数、仓库文件或聊天中；先在当前终端会话中设置 `OPEN_SHIFT_API_KEY`，运行后立即移除该环境变量。

已验证的 DeepSeek 示例：

```powershell
python -m open_shift probe-provider `
  --base-url "https://api.deepseek.com" `
  --model "deepseek-chat" `
  --protocol chat_completions `
  --response-format json_object
```

其他兼容端点可改用自己的地址和模型名：

```powershell
python -m open_shift probe-provider `
  --base-url "https://你的服务/v1" `
  --model "模型名" `
  --protocol chat_completions `
  --response-format json_object
```

`json_object` 只要求远端返回 JSON 对象，本地仍会执行完整字段、目标、地点和行动语义校验。对于明确支持严格 JSON Schema 的端点，可选择 `--response-format json_schema`，以获得更早的远端约束。

## GameMaker 本地桥接

阶段 3 的桥接服务只接受回环 IP 地址。令牌必须由启动器为每次游戏会话生成，并通过环境变量传给服务；不要把令牌提交到仓库或写入普通日志。

```powershell
$env:OPEN_SHIFT_BRIDGE_TOKEN = "至少16位的临时随机令牌"
python -m open_shift serve-bridge --host 127.0.0.1 --port 8711
```

固定连接场景、HTTP 合同与 GameMaker GML 已通过 UndertaleModTool 0.9.1.2 真实编译，并对补丁后的 `data.win` 副本完成二次读取和写回。补丁增加纯文字 `OPEN SHIFT` 菜单按钮；点击后从本地运行时 INI 读取临时令牌，通过 Async HTTP 获取固定三句场景，严格检查协议和资源白名单，再使用独立的 `draw_text_ext` 渲染器显示，最后复用原版 `out_to_title` 返回标题。

`game-patch/apply_mod.csx` 只接受 `manifest.json` 中记录的 Steam Windows 原版哈希，资源缺失或名称冲突会立即终止。构建时只对副本使用；不要直接把输出写到 Steam 安装目录。启动器及人工游戏内验收将在下一阶段完成。

只读检查原版补丁基线：

```powershell
python -m open_shift validate-patch-target `
  --data-win "E:\SteamLibrary\steamapps\common\VA-11 HALL-A\data.win" `
  --manifest "game-patch\manifest.json"
```

比较原版与参考 Mod 的名称级差异：

```powershell
python -m open_shift inspect-game-data `
  --data-win "原版data.win路径" `
  --compare "参考Mod的data.win路径"
```

命令只输出文件哈希、数据块尺寸和可识别资源名，不导出代码、贴图、音频或文本资源内容。

## 阶段 4 Launcher

Launcher 会为每次游戏会话生成随机桥接令牌、选择空闲回环端口、原子写入 GameMaker 运行时 INI、等待桥接健康检查、启动游戏，并在游戏退出后停止服务和删除 INI。API Key 只传给桥接子进程，不会传给游戏进程。

VA-11 HALL-A 的 GameMaker 本地目录通常为：

```text
%LOCALAPPDATA%\VA_11_Hall_A
```

这个旧版 Steam API 会相对于进程工作目录寻找 `.\Steam\Steam2.dll`。`--steam-root` 会验证正版 Steam 目录并只调整游戏子进程的工作目录；`--steam-app-id 447530` 阻止 Steam 将会话重启到登记的原版目录。游戏 EXE 和补丁资源仍从隔离副本加载，E 盘原版不会被修改。

使用工作区游戏副本启动确定性 MockProvider 世界：

```powershell
$env:PYTHONPATH = "src"

python -m open_shift launch `
  --db "work\playable-world.sqlite3" `
  --runtime-file "$env:LOCALAPPDATA\VA_11_Hall_A\open-shift-runtime.ini" `
  --game-cwd "reference-local\stage-4-game-copy" `
  --game-command "VA-11 Hall A.exe" `
  --steam-root "C:\Program Files (x86)\Steam" `
  --steam-app-id 447530 `
  --advance-minutes 1440
```

进入游戏后，点击主菜单左上角的 `+` 展开 Extra Chapters。`OPEN SHIFT` 使用与《后日谈》一致的蓝色章节项；点击后会向下展开黄色 `START` 项，再点击 `START` 连接本地世界服务。

使用 DeepSeek BYOK 世界时，先只在当前 PowerShell 会话设置 `OPEN_SHIFT_API_KEY`，然后在上述命令后增加：

```powershell
  --provider-base-url "https://api.deepseek.com" `
  --provider-model "deepseek-chat" `
  --provider-protocol chat_completions `
  --provider-response-format json_object
```

每个新的场景请求会推进一段持久世界时间，将最新 Agent 事件转换为固定三句安全场景。场景看完后，`player_scene_ack` 由服务端写入 SQLite。GameMaker 仍不能直接修改权威世界数据库。重复请求和服务重启不会重复推进世界或重复写入 ACK。

## 阶段 2 验收

阶段 2 的自动测试会运行 5 个 Agent 共 100 个游戏日，并检查：

- 邀请和承诺作为持久未来事件被兑现或拒绝，而不是一次性文本；
- 事件弧能够推进、解决并产生后续事件弧；
- Agent 完成目标后会主动创建合理的后续目标；
- 记忆检索具有角色隔离、相关性排序、确定性和字符预算；
- 连续运行与中断续跑得到相同社会状态和事件历史；
- 自主社会事件占比、重复率和数据库体积保持在测试阈值内。
