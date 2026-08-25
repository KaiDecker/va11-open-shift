# OPEN SHIFT 贡献指南

感谢你愿意参与 OPEN SHIFT。项目目标是把原版结局之后的酒吧生活扩展成一个可长期运行的
Agent 世界，同时保持原版角色核心、Jill 的玩家身份和 Steam 文件安全。

## 可以贡献什么

- 修复 GUI、安装器、启动器和原版流程中的体验问题
- 增加经过规则验证的城市事件、顾客安排和固定新闻
- 改进角色核心、对白合同、立绘白名单和回退文本
- 增加 SQLite 幂等、配对存档、日循环和长期 soak 测试
- 改进 README、安装说明、验收步骤和社区发布素材

阶段 20 的首个公开版本以 Windows x64 为目标；涉及 GameMaker、WebView2、PowerShell
或发行包的改动，请同时说明实际运行环境和兼容范围。

## 不要提交什么

- Steam 原版 `data.win`、游戏 EXE 或原版资源
- `reference-local/`、SQLite 数据库、API Key、运行时 INI 和生成的发行 EXE
- 把参考 Mod 的剧情、对白或世界设定直接搬入 OPEN SHIFT
- 让 Agent 改写不可变角色核心，或让未选择的候选分支影响世界状态

## 开发与验证

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

涉及 GameMaker 或发行包的改动，还应说明：

- Steam 原版是否保持只读
- 隔离副本是否完成 UTMT 往返验证
- 是否做了真实或 MockProvider 验收
- 是否新增了安全边界或迁移说明

## 提交 PR

PR 标题使用中文，例如：

```text
feat：完善发行包贡献说明
```

PR body 使用维护者模板的中文结构：`## 为什么`、`## 改动`、`## 验证`；只有确实
有长期价值时才增加其他章节。请在 PR 中保留可复现的测试结果，不要提交密钥或生成文件。
