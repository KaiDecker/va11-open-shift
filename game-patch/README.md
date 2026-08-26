# GameMaker 补丁源

这个目录只保存补丁元数据、UTMT 脚本和 GML 源码。这里不能出现 `data.win`、游戏 EXE、
提取出的精灵、字体、音频、原版对白或参考 Mod 资源。

## 安全边界

补丁只支持 `manifest.json` 中记录的 Steam Windows 原版哈希。哈希或注入点不匹配时必须
停止构建。构建过程先复制原版到临时目录，完成验证后才允许写入用户明确指定的隔离副本；
Steam 原版目录始终保持只读。

`apply_mod.csx` 是 UTMT 0.9.1.2 的补丁入口。它增加 Extra Chapters 的 OPEN SHIFT 入口，
复用原版 Jill 房间、酒吧、文本框、音乐选择、调酒器和存档页面。动态文字作为普通数据传输，
不会进入 `execute_string` 或原版命令解析器。

## 游戏流程

Python bridge 为每个场景提供 1–8 行经过校验的对白，并检查 speaker、portrait 和 expression。
Jill 是玩家角色，不会被加入自主 Agent 调度；Jill 说话时仍保留当前顾客的立绘。

第一天的准备状态显示在原版房间提示框中。玩家选择离开房间后，先播放开店前对白，再打开原版
音乐选择。只有点击原版 READY 关闭点唱机后，剧情游标才会继续推进。

调酒结果由 Python 规则层根据五种原料、冰、熟成和调制方式独立判断。收入、酒吧分数和剧情
结果只由规则层提交，GameMaker 不能自行发放收益。DeepSeek 只提供受限的行动或对白内容，
服务失败时使用安全回退。

中场休息沿用原版四头像存档页面。动态休息场景完成并收到 ACK 后才进入原版休息房间；保存
结束返回酒吧后，bridge 再请求下一位顾客，不伪造原版房间状态，也不发送重复 ACK。

## 保存与诊断

原版 24 个存档槽保持不变。每次保存会与 SQLite 世界快照配对，加载时验证原版存档哈希、
快照哈希、槽位身份和世界版本。启动器写入不含密钥的 `timing.log`，可用于区分 provider、
bridge 和 GameMaker 客户端的问题。

## 工具链

- UndertaleModTool / UTMT CLI 0.9.1.2；
- Steam Windows 原版 `data.win` SHA-256：
  `f14c4443838179f633f362c6fa20ca849d479c555eb315a507b4165ffa940991`；
- 所有构建和安装都必须使用隔离副本，不能修改 Steam 原版。
