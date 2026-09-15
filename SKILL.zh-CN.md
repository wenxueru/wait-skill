---
name: wait
description: 在 CodeWiz、Cursor、Claude Code、GitHub Copilot 和 Codex 中被动等待一个由命令报告的外部状态，无需使用模型轮次轮询。
---

# Wait

把一个外部对象的重复检查交给本地 `waitd` 管理服务，然后结束模型轮次，直到事件恢复当前会话。开始等待前阅读 [`waitd` 指南](docs/waitd.zh-CN.md)、[watcher 参考文档](docs/wait.zh-CN.md)和当前[客户端适配说明](docs/clients.zh-CN.md)。

## 调用方式

- `<调用> <条件>`：开始被动等待。
- `<调用> resume <watcher 日志>`：处理 watcher 事件。
- `<调用> status <watcher 日志>`：读取最近一次持久化的 watcher 结果，但不重启 watcher。

Codex 使用 `$wait`；其他客户端使用其斜杠 Skill 形式，通常为 `/wait`。

## 工作流程

1. **选择等待方式。** 确定只读查询、精确的 ready 与 terminal 值和有限超时，默认一小时。更长的等待按[等待时限](docs/wait.zh-CN.md#等待时限)评估任务稳定性及失败、停滞检测能力；需要定期检查健康与进展时，考虑 [wait-loop](wait-loop/SKILL.zh-CN.md)。
2. **准备。** 复用或创建任务的原生 Todo 条目；goal 节点或 loop 迭代沿用调用方条目。为该对象和会话选择唯一的 lock 与 log 文件，准备 `wait resume` 消息模板，包含日志路径、`{event_id}`、`{event}` 和 `{status}`。
3. **启动。** 通过 `scripts/waitctl.py start -- ...` 提交，传入 `--client` 和当前 `--session`，命令会按需启动服务。通过 `show WATCH_ID` 和存活的 watcher lock 确认所有权；提交成功只表示已注册。
4. **挂起。** 将 Todo 标记等待，记录条件、截止时间和日志路径。按[客户端适配](docs/clients.zh-CN.md)接好所属会话的事件投递通道，再结束轮次。
5. **恢复。** 校验持久日志和 event ID，查询一次当前状态，再决定下一步。依据核实结果更新 Todo，只有满足验收条件时才能完成条目。goal 所属等待由根 Agent 先记录 DAG 转换。

## 边界

- 查询必须只读，并且不得隐式调用 shell。
- 唤醒不会授权重试、重启、部署或以其他方式修改外部系统。
- 不要为同一对象和任务启动重复 watcher。
- 相同 event ID 的重复通知应视为重复事件，不得重复已经完成的工作。
- terminal、timeout 和连续查询失败应当报告，而不是无限重试。
- 每个 watcher 都必须有有限的总超时，避免错误条件导致进程泄漏或任务永久停滞。

运行 `python scripts/waitctl.py --help` 和 `python scripts/wait_for.py --help` 查看 CLI 详情。
