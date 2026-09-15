---
name: wait-loop
description: 在 CodeWiz、Cursor、Claude Code、GitHub Copilot 和 Codex 中，通过父级 wait Skill 安排有界定时等待，实现 Loop 模式。用户直接调用 wait-loop，或已明确调用的 wait-goal 由根 Agent 主动选择它处理必要的长等待时使用；不得从普通重复或长时间任务中自动推断。
---

# Wait Loop

按固定间隔重复执行任务。

通过 `../scripts/waitctl.py loop -- ...` 管理持久化循环状态；服务使用父级 `wait` 的实现注册计时器。服务把循环校验和持久化交给 `wait_loop.py`。开始前阅读[循环协议](../docs/wait-loop.zh-CN.md)、[`waitd` 指南](../docs/waitd.zh-CN.md)、[watcher 协议](../docs/wait.zh-CN.md)和[客户端适配](../docs/clients.zh-CN.md)。

## 调用方式

- `<调用> <间隔>: <任务>`：启动循环，并立即执行第一轮。
- `<调用> resume <状态文件>`：处理定时事件。
- `<调用> status <状态文件>`：只报告状态，不推进循环。
- `<调用> cancel <状态文件>`：取消后续迭代。

其中 `<调用>` 按客户端使用 `$wait-loop` 或 `/wait-loop`。

## 协议

1. 使用任务、间隔、客户端、会话和有限的总 `--duration` 运行 `waitctl.py loop -- init`，保留返回的 `state_file`。默认 24 小时只是安全上限；能确定合理期限时应明确设置。用户指定有限轮数时再传 `--max-iterations`。
2. 立即执行一次已保存的任务。每轮都由根会话执行，不创建脱离控制的子 Agent 循环。
3. 本轮成功后运行 `complete --summary`。返回 `completed` 时汇报最终结果并停止；否则保留 `watch_id` 和 `next_run_at`。
4. 服务在 `complete` 返回前注册计时器。使用保存的 watch ID，按[客户端适配](../docs/clients.zh-CN.md)接好事件投递通道。结束轮次，由客户端适配器把 loop 状态、watcher 日志和 event ID 送回会话。服务重启时会补齐状态提交后中断的计时器注册。
5. 收到 `ready` 后校验 watcher 日志，并运行 `begin --event-id`。返回 duplicate 时不得重复执行；如果消息送达时已经超过 loop 截止时间，`begin` 会结束 loop，此时报告完成并停止；否则执行下一轮并回到步骤 3。
6. 收到 `Expired` 后运行 `expire --event-id` 并停止。如果等待期间循环被取消、完成或替换，所有权校验会直接停止旧 watcher，不再额外唤醒模型。

## 边界

- 每个 loop 有有限总时长，每个定时 watcher 也有独立的有限超时。
- 定时唤醒只允许恢复已保存的循环，不会扩大循环任务执行外部副作用的权限。
- 同一时间最多执行一轮。先持久化本轮完成，再创建下一 watcher。
- 本轮失败或中断时不得标记完成或静默重试；应报告并询问继续还是取消。
- 状态中已有活动 `watch_id` 时，不得启动第二个 watcher。

运行 `python ../scripts/waitctl.py --help` 和 `python ../scripts/waitctl.py loop -- --help` 查看命令。
