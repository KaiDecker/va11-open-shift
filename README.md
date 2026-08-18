# Open Shift Simulator

这是 VA-11 HALL-A 多 Agent 永续世界 Mod。当前已完成 BYOK 行动与对白闭环、多 Agent 社会模拟、可玩 GameMaker 本地桥接和可复现补丁源码。仓库不会包含或分发游戏资源。

当前能力：

- SQLite 持久化世界状态、事件、记忆、关系、目标和待处理事件
- 事件驱动的世界时间，不按每秒轮询所有角色
- 模型供应商接口、完全确定性的 `MockProvider` 与 BYOK HTTP Provider
- 已验证的兼容 Chat Completions 协议，以及可选的 Responses 风格隔离适配器
- 严格 JSON 行动与对白校验、独立单次探针、共享调用预算和安全故障兜底
- 白名单行动与规则验证，Provider 不能直接修改数据库
- Dana、Dorothy、Alma、Stella、Sei 五个持久 Agent
- Agent 私有、相关且有固定上下文预算的确定性记忆检索
- 消息、邀请、承诺、双向关系后果和可持续事件弧
- 目标完成后创建后续目标，世界不依赖玩家触发即可继续演化
- 30/100 天无人值守模拟、断点续跑和确定性回放测试
- 仅监听本机回环地址、带令牌和幂等语义的 GameMaker HTTP 桥接协议
- 原版 `data.win` 只读盘点、哈希基线和按资源名验证的补丁合同
- UndertaleModTool 0.9.1.2 补丁脚本，以及复用原版资源的 Extra Chapters 入口
- 原版章节字体、过渡、酒吧房间、对白框、逐字显示、角色立绘、表情和嘴型
- 安全 launcher、动态 Agent 世界场景、服务重启幂等和玩家观看结果回写
- 逐角色生成的中文对白，每轮只向模型提供当前发言者的私有观察和公开对话历史
- Jill 专用玩家对白上下文：可以在原版对白框发言但不显示立绘，也不会进入 Agent 行动调度
- 复用原版配方书与调酒器，将五种配料、冰、陈化和调制方式交给本地规则层判定
- 原版 25 种调制饮品的结构化配方，以及 `exact`、`acceptable`、`wrong`、`special` 四类服务结果
- 每日最多三位顾客的持久有限剧情图；每笔点单只缓存四类有意义结果并在局部汇合
- 只有实际出杯选中的分支会提交记忆、收入和剧情游标；就绪场景重启后不再调用 Provider
- 首次进入并行生成第一天，以无说话人环境文字和门铃自然衔接；游玩当天仅后台预取下一天
- 基于官方站点、VNDB 和角色资料整理的结构化角色核心、人物／立绘／表情白名单
- 原作角色核心不可自行改写；看完的生成对白会成为参与 Agent 的私有长期记忆，固定回退文本不会被学习

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
- 对白按发言轮次分别调用 Provider；每轮只序列化当前角色的状态、关系、目标、相关记忆和已公开台词。
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

对白探针同样只调用一次，不创建或修改世界数据库：

```powershell
python -m open_shift probe-dialogue `
  --base-url "https://api.deepseek.com" `
  --model "deepseek-chat" `
  --protocol chat_completions `
  --response-format json_object
```

成功输出必须是简体中文，并且只包含固定发言者、白名单表情与普通文本。Jill 不会作为 Agent 被模型选择；游戏场景中的 Jill 台词使用独立玩家上下文，且不会替玩家选择配方或宣称调酒结果。

## GameMaker 本地桥接

阶段 3 的桥接服务只接受回环 IP 地址。令牌必须由启动器为每次游戏会话生成，并通过环境变量传给服务；不要把令牌提交到仓库或写入普通日志。

```powershell
$env:OPEN_SHIFT_BRIDGE_TOKEN = "至少16位的临时随机令牌"
python -m open_shift serve-bridge --host 127.0.0.1 --port 8711
```

HTTP 合同与 GameMaker GML 通过 UndertaleModTool 0.9.1.2 构建。补丁把 `OPEN SHIFT` 章节文字追加到原版 `extrachapter_text` 的绘制事件，使用原版 `dialogfont2` / `ch_small` 等小号字体；点击 `START` 后复用 `out_of_apartment`、`towork_load` 和 `bar` 的原版过渡链。在酒吧房间内，桥接控制器严格检查协议与人物白名单，再用原版 `obj_textbox`、逐字显示和人物对象呈现场景。模型文本不会进入原版命令解析器。

`game-patch/apply_mod.csx` 只接受 `manifest.json` 中记录的 Steam Windows 原版哈希，资源缺失或名称冲突会立即终止。构建时只对副本使用；不要直接把输出写到 Steam 安装目录。

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

阶段 9 的安装工具只允许把已验证的补丁输出写入隔离副本。它会先核对
Steam 原版 `data.win` 的 manifest 哈希，再原子备份现有副本；卸载时如果
安装后的文件被其他程序改动，会拒绝盲目恢复。Steam 安装目录不会作为目标
路径被接受：

```powershell
python -m open_shift install-patch `
  --original-data-win "E:\SteamLibrary\steamapps\common\VA-11 HALL-A\data.win" `
  --patched-data-win "临时目录\stage-9-patched.data.win" `
  --destination-data-win "reference-local\stage-4-game-copy\data.win" `
  --backup-dir "reference-local\stage-9-backups" `
  --record "reference-local\stage-9-install.json" `
  --manifest "game-patch\manifest.json"

python -m open_shift uninstall-patch `
  --record "reference-local\stage-9-install.json"
```

本地运行配置使用不含密钥的 TOML；API Key 仍只从环境变量读取。配置校验只
输出脱敏后的字段，并且最多允许预取一天：

```powershell
python -m open_shift validate-config `
  --config "配置目录\open-shift.toml"
```

发布前检查项见仓库根目录的 `RELEASE_CHECKLIST.md`；它覆盖哈希、UTMT
往返、安装备份、卸载恢复、配置脱敏、游戏验收和 30/365 日 soak。

启动器也可以直接读取同一份配置：

```powershell
python -m open_shift launch `
  --config "配置目录\open-shift.toml" `
  --db "work\playable-world.sqlite3" `
  --runtime-file "$env:LOCALAPPDATA\VA_11_Hall_A\open-shift-runtime.ini" `
  --game-cwd "reference-local\stage-4-game-copy" `
  --game-command "VA-11 Hall A.exe" `
  --steam-root "C:\Program Files (x86)\Steam" `
  --steam-app-id 447530
```

示例配置（保存为 `open-shift.toml`）：

```toml
[provider]
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
protocol = "chat_completions"
response_format = "json_object"
api_key_env = "OPEN_SHIFT_API_KEY"
timeout_seconds = 30
max_calls = 100000
thinking = "disabled"

[world]
prefetch_days = 1
```

## 阶段 4-8 Launcher、Agent 对话、调酒、每日剧情图与配对存档

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
  --native-save-dir "$env:LOCALAPPDATA\VA_11_Hall_A\saves" `
  --paired-save-dir "$env:LOCALAPPDATA\VA_11_Hall_A\open-shift-paired-saves" `
  --advance-minutes 1440
```

进入游戏后，点击主菜单左上角的 `+` 展开 Extra Chapters。`O.S.` 使用原版蓝色章节项和小号章节字体；点击后会向下展开黄色 `START` 项。再点击 `START` 会播放原版离场过渡、经过日期加载画面进入酒吧，然后由原版对白框和人物立绘显示 Agent 场景。Jill 仍是玩家视角：她可以像原作一样在对白框中说话，但没有人物立绘；Jill 发言时会保留上一位在场顾客的立绘。她不会作为自主 Agent 出门、消费或替玩家行动。

使用 DeepSeek BYOK 世界时，先只在当前 PowerShell 会话设置 `OPEN_SHIFT_API_KEY`，然后在上述命令后增加：

```powershell
  --provider-base-url "https://api.deepseek.com" `
  --provider-model "deepseek-chat" `
  --provider-protocol chat_completions `
  --provider-response-format json_object
```

每个新的场景请求会推进一段持久世界时间，并从最新权威事件选择参与者和当前顾客。世界从原版主线及结局事件发生之后继续运转；已经建立的人物关系和生活状态不会被重置。顾客先明确点单，Jill 使用独立玩家上下文确认，随后游戏切入原版配方书与调酒界面。当前值班只有 Jill 执行配料操作、调制和出杯；包括 Dana 在内的 Agent 只能点单、交谈、提醒或评价，不能宣称代替玩家调酒。模型每次只负责当前角色的一句：Agent 只能看到自己的角色核心、定性状态、相关长期记忆和此前公开台词；Jill 只能看到公开对白、固定角色核心和规则层确认的服务结果。金钱、目标数值和数据库时间不会进入对白观察。

玩家出杯后，GameMaker 只上传 Adelhyde、Bronson Extract、Powdered Delta、Flanergide、Karmotrine 的数量，以及冰、陈化和调制方式。Python 根据原版配方谓词重新识别饮品，再判定为准确、可接受、错误或加大杯特别服务；模型和 GameMaker 都不能自行宣称对错。判定会幂等写入 SQLite，随后作为不可改写的事实生成顾客与 Jill 的分支反应。单句最多 72 个字符，GameMaker 使用原版字体按酒吧左侧对白区域的 380 像素宽度插入原生换行。未配置 Provider、超时或输出不合规时使用确定性中文回退场景；调用预算耗尽会单独提示。

DeepSeek V4 Flash 应使用模型 `deepseek-v4-flash`，并显式传入 `--thinking disabled`（探针）或 `--provider-thinking disabled`（游戏启动器），避免默认思考占用对白输出预算和等待时间。思考开关默认不发送，因此不会改变其他兼容端点的既有请求。

最终场景和服务结果会完整序列化到 SQLite。重复请求和服务重启直接重放已保存内容，不会再次调用 API、重复推进世界或重复结算一杯酒。每句的 `speaker_id` 必须匹配白名单人物；Agent 立绘和表情只允许映射到已核对的原版状态。Python 与 SQLite 中 Jill 始终使用 `portrait_id: null`，只有发给旧版 GameMaker JSON 解码器的 HTTP 响应会将其转换为空字符串。场景看完后，服务端会幂等写入 `player_scene_ack`；真实生成的公开谈话会被压缩为情景记忆，分别写入参与 Agent 的私有记忆流。Jill 的对白可以被在场 Agent 记住，但 Jill 自己没有 Agent 私有记忆或自主行动循环。GameMaker 仍不能直接修改权威世界数据库。

阶段 7 已将单次调酒扩展为可恢复的整日有限剧情图。每天最多三位顾客，每笔点单预生成 `exact`、`acceptable`、`wrong`、`special` 四条结果草稿并汇合；Python 规则层在出杯时选择唯一分支，未选择内容不会写入事件、关系、目标、金钱或记忆。收入使用原版 25 种配方价格（例如 Moonblast 为 180）；可放大的大杯沿用原版基础价加 100 的规则，原本固定大杯的配方不重复加价，错误饮品为 0。GameMaker 只把服务端返回值应用到原版 `cashcounter`、`barscore`，并同步原版短暂收入弹出数字。

首次进入存档以及每个新营业日时，前台使用无姓名、无立绘的冰箱、雨声和酒杯环境文字；准备完成后以“门铃响了”接入第一位顾客。环境行的 `speaker_id` 和 `portrait_id` 在 Python 与 SQLite 中保持 `null`，仅在发给旧版 GameMaker 的 HTTP JSON 中转换为空字符串。生成失败会显示 `story_generation_failed` 安全诊断，重新进入触发同一批源事件的恢复重试。当天开始游玩后只预取下一营业日，不会无限生成或持续消耗 API。DeepSeek V4 Flash 在仅提供 DeepSeek 地址时默认使用 `deepseek-v4-flash`，并在未显式覆盖时关闭 thinking。

阶段 8 复用原版 24 个 `Record of Waifu Wars[槽位].txt` 槽位。保存 Open Shift 时先完成原版保存，再通过 SQLite backup 建立不可变 Agent 世界快照并原子更新槽位指针；读取时先核对原版存档哈希、快照哈希、槽位和世界修订号，全部匹配后才进入原版加载流程。覆盖保存失败会恢复上一份成对的原版存档，恢复失败会回滚实时世界，配对/恢复请求即使桥接服务重启也不会重复执行。

最后一位顾客离开后会显示打烊和按真实服务结果累计的收入结算，随后 Jill 回到原版房间；手机、新闻和房间功能保持正常，玩家通过平板的 Data 图标进入原版 Save/Load 首页，再选择 Save 打开 24 槽页面。如果尚未保存就点击“去酒吧上班”，游戏会沿同一条原版 Data 图标动画引导到存档入口，而不会跳过恢复点。当天状态先停在 `save_required`；原版槽位和 SQLite 快照都成功后只关闭存档页面，Jill 仍留在房间，必须由玩家再次点击原版“去酒吧上班”按钮才进入下一营业日。当前日、开店前奏是否看过、已提交分支、预取状态、当日收入和恢复点都会随配对槽位恢复。

## 阶段 2 验收

阶段 2 的自动测试会运行 5 个 Agent 共 100 个游戏日，并检查：

- 邀请和承诺作为持久未来事件被兑现或拒绝，而不是一次性文本；
- 事件弧能够推进、解决并产生后续事件弧；
- Agent 完成目标后会主动创建合理的后续目标；
- 记忆检索具有角色隔离、相关性排序、确定性和字符预算；
- 连续运行与中断续跑得到相同社会状态和事件历史；
- 自主社会事件占比、重复率和数据库体积保持在测试阈值内。
