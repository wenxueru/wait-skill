---
name: wait-loop
description: 在 CodeWiz、Cursor、Claude Code、GitHub Copilot 和 Codex 中，通过父级 wait Skill 安排有界定时等待，实现 Loop 模式。用户直接调用 wait-loop，或根 Agent 在 wait、wait-goal 的长等待中主动选择定期健康检查时使用。
---

# Wait Loop

按固定间隔重复执行任务，不假设用户在场；不要主动提问或调用交互式提问工具。

通过 `../src/waitctl.py loop -- ...` 管理循环，服务用 `wait` 安排下一轮。开始前阅读[循环协议](../docs/wait-loop.zh-CN.md)和[当前客户端说明](../docs/clients.zh-CN.md)；计时细节见 [wait](../docs/wait.zh-CN.md)，服务命令见 [waitd](../docs/waitd.zh-CN.md)。

## 调用方式

- `<调用> <间隔>: <任务>`：启动循环，并立即执行第一轮。
- `<调用> resume <状态文件>`：处理定时事件。
- `<调用> status <状态文件>`：只报告状态，不推进循环。
- `<调用> cancel <状态文件>`：取消后续迭代。

其中 `<调用>` 按客户端使用 `$wait-loop` 或 `/wait-loop`。

## 协议

1. 运行 `waitctl.py loop -- init`，传入任务、间隔、客户端、会话、有限的总 `--duration`，以及简短单行的 `--event-note`，说明计时唤醒后要做什么；保留返回的 `state_file`。每次计时会用 `$wait-loop resume {state_file}; event_id={event_id}; log_file={log_file}; event_note="{event_note}"` 唤醒。默认总时长为 24 小时；任务不需要那么久时，设置更短的期限。用户指定有限轮数时再传 `--max-iterations`。
2. 立即执行一次已保存的任务，任务与已有授权范围内的可逆选择采用合理默认值。每轮都由根会话执行，不创建脱离控制的子 Agent 循环。调用 wait 时沿用本轮任务的 Todo 条目。
3. 处理本轮结果：
   - 成功：运行 `complete --summary`。返回 `completed` 时汇报结果并停止；否则保留 `watch_id` 和 `next_run_at`。
   - 失败、中断或缺少关键输入、授权：保持 `running`，报告阻塞原因与状态文件路径并结束轮次，不调用 `complete` 或静默重试。用户给出方向后从保存状态恢复。
4. `complete` 返回前，服务会安排好下一次计时。用它的 watch ID 按[客户端说明](../docs/clients.zh-CN.md)接好唤醒，再结束轮次。重启后服务补齐遗漏的计时器注册；已经启动但中断的程序报告中断，不自动重跑。计时失败按[恢复协议](../docs/wait-loop.zh-CN.md#恢复与取消)检查。
5. 校验计时日志：`event: exited`、`exit_code: 0` 且 stdout 为 `Ready` 时，运行 `begin --event-id`。返回 duplicate 时不得重复执行；如果消息送达时已经超过 loop 截止时间，`begin` 会结束 loop，此时报告完成并停止；否则执行下一轮并回到步骤 3。
6. 计时程序的 stdout 为 `Expired` 时运行 `expire --event-id` 并停止。如果等待期间循环被取消、完成或替换，所有权校验会直接停止旧 watcher，不再额外唤醒模型。

## 边界

- 每个 loop 有有限总时长，每个定时 watcher 也有独立的有限超时。
- 定时唤醒只允许恢复已保存的循环，不会扩大循环任务执行外部副作用的权限。
- 同一时间最多执行一轮。先持久化本轮完成，再创建下一 watcher。
- 状态中已有活动 `watch_id` 时，不得启动第二个 watcher。

运行 `python ../src/waitctl.py --help` 和 `python ../src/waitctl.py loop -- --help` 查看命令。
