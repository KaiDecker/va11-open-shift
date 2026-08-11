# Open Shift Simulator

这是 VA-11 HALL-A 多 Agent 永续世界 Mod 的阶段 0 原型。当前工程只实现无界面的世界模拟核心，不修改游戏文件，也不连接真实模型 API。

当前能力：

- SQLite 持久化世界状态、事件、记忆、关系、目标和待处理事件
- 事件驱动的世界时间，不按每秒轮询所有角色
- 模型供应商接口与完全确定性的 `MockProvider`
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
- 当前不包含 OpenAI 或其他真实 API 实现。BYOK 接入属于后续阶段。
