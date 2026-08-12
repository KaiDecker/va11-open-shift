# Open Shift Simulator

这是 VA-11 HALL-A 多 Agent 永续世界 Mod 的无界面世界模拟核心。当前已完成 BYOK 决策闭环与阶段 2 多 Agent 社会模拟；尚未修改游戏文件或接入 GameMaker。

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

## 阶段 2 验收

阶段 2 的自动测试会运行 5 个 Agent 共 100 个游戏日，并检查：

- 邀请和承诺作为持久未来事件被兑现或拒绝，而不是一次性文本；
- 事件弧能够推进、解决并产生后续事件弧；
- Agent 完成目标后会主动创建合理的后续目标；
- 记忆检索具有角色隔离、相关性排序、确定性和字符预算；
- 连续运行与中断续跑得到相同社会状态和事件历史；
- 自主社会事件占比、重复率和数据库体积保持在测试阈值内。
