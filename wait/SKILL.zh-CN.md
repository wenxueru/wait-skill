---
name: wait
description: 被动等待一个由命令报告的外部状态，无需使用模型轮次进行轮询。适用于 CI、部署、队列、作业或服务就绪等待。依赖图或协调式多步骤工作应使用 wait-goal。
---

# Wait

把一个外部对象的重复检查交给 `../scripts/wait_for.py`，然后结束模型轮次，直到事件唤醒当前任务。开始等待前阅读 [watcher 参考文档](../docs/wait.zh-CN.md)。

## 调用方式

- `$wait <条件>`：开始被动等待。
- `$wait resume <watcher 日志>`：处理 watcher 事件。
- `$wait status <watcher 日志>`：读取最近一次持久化的 watcher 结果，但不重启 watcher。

## 工作流程

1. 确定一个只读查询命令、精确的 ready 值、terminal 值，以及用户要求的总超时时间。
2. 为该外部对象和 Codex 任务选择唯一的 lock 与 log 文件。不得在命令参数、日志或通知中写入凭据。
3. 使用当前 thread ID 启动 `../scripts/wait_for.py`。长时间等待应使用当前环境可靠支持的进程管理器；只有确认已安装 `tmux` 后才优先使用它。
4. 设置显式调用 `$wait resume` 的消息模板，写明 watcher 日志，并包含 `{event_id}`、`{event}` 和 `{status}`。watcher 会持久化投递进度；确定失败和结果不明的客户端超时都会使用同一稳定 ID 重试，接收方据此安全去重重复唤醒。
5. watcher 取得 wait 所有权后，不要再从模型轮次查询同一对象。结束当前轮次。
6. 恢复后读取持久日志，并查询一次当前状态。通知只是提示，不是事实证明。

## 边界

- 查询必须只读，并且不得隐式调用 shell。
- 唤醒不会授权重试、重启、部署或以其他方式修改外部系统。
- 不要为同一对象和任务启动重复 watcher。
- 相同 event ID 的重复通知应视为重复事件，不得重复已经完成的工作。
- terminal、timeout 和连续查询失败应当报告，而不是无限重试。
- 涉及多个步骤、Agent、依赖或目标级验收时，使用 `$wait-goal`。

运行 `python ../scripts/wait_for.py --help` 查看 CLI 详情。
