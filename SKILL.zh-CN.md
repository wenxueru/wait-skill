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

1. 确定一个只读查询命令、精确的 ready 与 terminal 值，以及有限的总超时时间。无法确定更合理的上限时才使用默认 24 小时。
2. 为该外部对象和 Agent 会话选择唯一的 lock 与 log 文件。不得在命令参数、日志或通知中写入凭据。
3. 使用 `scripts/waitctl.py start -- ...` 提交 watcher，并传入 `--client` 和当前 `--session`。该命令会按需启动唯一的本地 `waitd` 服务。只有兼容或故障恢复时才直接运行 `wait_for.py`。
4. 设置显式调用 `wait resume` 的消息模板，写明 watcher 日志，并包含 `{event_id}`、`{event}` 和 `{status}`。watcher 会持久化投递进度。Codex 队列失败使用稳定 ID 重试，最多 12 次；会话恢复型客户端默认只尝试一次，因为超时不代表该轮次没有启动。
5. watcher 取得 wait 所有权后，不要再从模型轮次查询同一对象。结束当前轮次。
6. 恢复后读取持久日志，并查询一次当前状态。通知只是提示，不是事实证明。

## 边界

- 查询必须只读，并且不得隐式调用 shell。
- 唤醒不会授权重试、重启、部署或以其他方式修改外部系统。
- 不要为同一对象和任务启动重复 watcher。
- 相同 event ID 的重复通知应视为重复事件，不得重复已经完成的工作。
- terminal、timeout 和连续查询失败应当报告，而不是无限重试。
- 每个 watcher 都必须有有限的总超时，避免错误条件导致进程泄漏或任务永久停滞。

运行 `python scripts/waitctl.py --help` 和 `python scripts/wait_for.py --help` 查看 CLI 详情。
