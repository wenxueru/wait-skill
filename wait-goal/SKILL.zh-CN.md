---
name: wait-goal
description: 在 CodeWiz、Cursor、Claude Code、GitHub Copilot 和 Codex 中，把用户明确调用的 Goal 模式实现为持久、事件驱动的 DAG。仅当用户直接调用 wait-goal 时使用；不得按任务长度或复杂度自动选择。
---

# Wait Goal

将目标作为持久化依赖图运行。

通过 `../scripts/waitctl.py goal -- ...` 管理持久图状态，并使用 `../scripts/waitctl.py start -- ...` 提交外部 watcher。服务把图校验交给 `wait_goal.py`，把 watcher 语义交给 `wait_for.py`。开始前阅读[目标协议](../docs/wait-goal.zh-CN.md)、[`waitd` 指南](../docs/waitd.zh-CN.md)、[watcher 协议](../docs/wait.zh-CN.md)和[客户端适配说明](../docs/clients.zh-CN.md)。

## 调用方式

- `<调用> <目标>`：启动目标。
- `<调用> resume <状态文件>`：收到唤醒消息或用户请求后恢复目标。
- `<调用> status <状态文件>`：报告持久化状态，但不推进目标。
- `<调用> pause|cancel <状态文件>`：执行相应的控制操作。

其中 `<调用>` 按客户端使用 `$wait-goal` 或 `/wait-goal`。

## 核心行为

1. **规划。** 使用 `init` 持久化目标，保留返回的 `state_file`，加入初始节点。每个节点有一个有界结果、依赖支持的输入、验收检查、预期产物，以及只读或明确的写入范围。紧密耦合的工作放在同一节点。有原生 Todo 或计划工具时，用节点 ID 展示这些任务，并加入最终目标验收条目。
2. **加载与调度。** 启动或恢复时读取 `show`、运行 `check`，从持久状态重建当前任务的 Todo。只执行 `ready` 返回的节点。状态转换成功后同步 Todo；调度和完成判定依据持久图与验收证据。
3. **执行。** local 和 external 节点先 `start` 再执行。agent 节点先持久化 `prepare-agent`，把 dispatch token 写入子任务，再用 `start --dispatch-token ... --agent-id ...` 关联返回的运行时 ID。将已派发工作标为进行中。要求子 Agent 用独立 Todo 管理内部步骤，向根 Agent 返回结果、证据、产物和新发现的工作。共享 Todo 由根 Agent 维护；子 Agent 不相互联系或等待，不修改图、调用 goal 命令或创建 Agent。
4. **验收与演化。** 检查结果和预期产物后运行 `complete`；失败使用 `fail` 记录。状态命令成功后更新对应 Todo。新发现的工作使用 `add --reason` 加入图和 Todo，再调度新就绪节点。子清单完成代表结果可提交根 Agent 验收。
5. **等待与恢复。** 没有 ready 节点时，根据当前活动处理：
   - `dispatching`：先用保存的 token 对照运行时 Agent，再进行后续派发。
   - Agent 运行中：使用运行时阻塞等待，接收完成或需要处理的事件。
   - 外部等待：准备 `wait`，用返回的绝对路径提交有界 watcher，确认启动回执后 `activate-wait`。在 Todo 注明等待条件和截止时间，然后结束轮次。收到事件后读取日志、记录 `wake`、复查外部状态，再完成、失败或建立下一次等待。
   - `orphaned_wait` 或准备中断：用当前 watch ID 执行 `abort-wait`，再为同一节点建立替代 watcher。失败工作需要继续时，决定恢复后 `retry` 原节点。
   - 依赖阻塞或需要用户决定：在 Todo 记录原因，报告所需决定。
6. **控制。** 暂停、恢复、重试和取消先作用于持久状态，再刷新受影响的 Todo。恢复时使用已保存的 dispatch 与 watch ID 核对未完成工作。
7. **最终验收。** 所有节点完成后，对照原始目标检查结果。缺工作则加入并继续；否则用 `verify` 持久化证据，再运行 `finish`。两个命令成功后完成最终验收 Todo 条目。

## 不变量

- 等待必须由事件驱动。不要为未变化的状态消耗模型轮次，也不要定期发送进度消息。
- 尽量避免判断“超过一小时后才能进行下一步”。先重新运行 `check` 和 `ready`，处理已返回的结果与本地检查，并寻找仍可推进目标的独立工作。确实需要非常长时间的外部等待时，应提示根 Agent 可以使用 `wait-loop`，约每小时执行一次有界只读检查。通过 `loop init --goal-state FILE --goal-node ID` 绑定监控，在保存的任务中说明检查和向根 Agent 汇报的内容。服务跟踪关联，并在目标离开 open 状态或节点结束时取消监控。
- 图可以演化，但必须保持无环，并且每个依赖都必须存在。
- 状态修改以原子方式追加到目标事件历史中。执行期间发现新工作时，使用 `--reason` 说明原因。
- 通知只是提示，不是事实证明；采取行动前重新检查当前状态。
- 查询命令必须只读。唤醒不会授予重试、部署、重启或以其他方式修改外部系统的权限。
- 不要只为并发形式而 fan-out。对于依赖清晰、上下文可隔离、可独立验收且写入范围不冲突的节点，应尽量并发派发；这既能缩短关键路径，也可能减少根 Agent 重复处理上下文的 Token。短小的顺序链、紧密耦合或写入重叠的工作，在协调成本高于并发收益时应由一个 Agent 完成。
- 只有确认 prepared dispatch 没有创建存活的子 Agent，或已终止该 Agent 后，才能运行 `abort-agent`。
- 凭据不得写入 argv、图状态、watcher 日志或通知。
- 激活 watcher 后，按[客户端适配](../docs/clients.zh-CN.md)接好目标初始化时记录的会话的事件投递通道，再结束轮次。

运行 `python ../scripts/waitctl.py --help`、`python ../scripts/wait_goal.py --help` 和 `python ../scripts/wait_for.py --help` 查看命令详情。
