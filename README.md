# Open Shift Simulator

这是 VA-11 HALL-A 多 Agent 永续世界 Mod 的阶段 0 原型。当前工程只实现无界面的世界模拟核心，不修改游戏文件，也不连接真实模型 API。

当前能力：

- SQLite 持久化世界状态、事件、记忆、关系、目标和待处理事件
- 事件驱动的世界时间，不按每秒轮询所有角色
- 模型供应商接口、完全确定性的 `MockProvider` 与 BYOK HTTP Provider
- Responses/兼容 Chat Completions 两种隔离协议
- 严格 JSON 行动校验、单次探针、调用预算和安全故障兜底
- 白名单行动与规则验证，Provider 不能直接修改数据库
- Dana、Dorothy、Alma 三个示例 Agent
- 30 天模拟、断点续跑和确定性回放测试

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
- 远程端点必须使用 HTTPS，明文 HTTP 只允许本机回环地址。
- 当前开发环境无法打开官方 OpenAI 文档，因此真实请求形状尚未做现场验证；使用前应依据当时官方文档核对，并先运行一次 `probe-provider`。

## BYOK 单次探针

探针只请求一次决策，不创建或修改世界数据库。不要把 API Key 放在命令参数、仓库文件或聊天中；先在当前终端会话中设置 `OPEN_SHIFT_API_KEY`，运行后立即移除该环境变量。

Responses 协议示例：

```powershell
python -m open_shift probe-provider `
  --base-url "https://api.openai.com/v1" `
  --model "你账户中实际可用的模型" `
  --protocol responses
```

兼容端点可改用：

```powershell
python -m open_shift probe-provider `
  --base-url "https://你的服务/v1" `
  --model "模型名" `
  --protocol chat_completions `
  --response-format json_object
```

`json_object` 只要求远端返回 JSON 对象，本地仍会执行完整字段、目标、地点和行动语义校验。对于支持严格 JSON Schema 的端点，保留默认的 `json_schema` 可获得更早的远端约束。
