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

1. 在开始实质工作前持久化目标。让 `init` 在 `/tmp/.wait-goal/` 下创建按项目隔离的默认状态，保留返回的 `state_file`，再向其中写入初始节点；只有需要指定位置时才传 `--state`。新发现的工作应加入图中成为节点，不要只保留在对话上下文里。
2. 让每个节点尽可能独立：只产生一个有界结果，使用明确且由依赖支持的 `--input`、验收检查和预期产物，并声明 `--read-only` 或具体写入路径。只有不可避免的执行顺序或信息流才使用依赖边；紧密耦合的工作应合并到同一节点。
3. 根 Agent 通过 `waitctl.py goal -- ...` 运行 goal 命令：先执行 `check`，然后只执行 `ready` 返回的节点。服务只串行执行命令，不决定图修改。ready frontier 会自动串行化写入范围重叠或未声明范围的节点。只有根 Agent 可以调度工作并请求修改图。根 Agent 可以并行执行相互独立的节点，但每个子 Agent 只能向根 Agent 汇报；不得联系或等待其他子 Agent，不得修改图、调用 goal 命令或创建 Agent。对于 agent 节点，先持久化 `prepare-agent`，把返回的 dispatch token 写入子任务，再使用 `start --dispatch-token ... --agent-id ...` 关联运行时返回的 ID。
4. 只有节点的验收检查通过且预期产物存在时，才能将其标记为完成。在状态中记录简洁结果和产物，再调度新进入 ready 的节点。
5. 没有 ready 节点时：
   - 如果仍有 Agent 在运行，使用运行时提供的阻塞式 Agent 等待，只在 Agent 完成或需要处理时恢复。如果 activity 为 `dispatching`，采取其他操作前必须用保存的 dispatch token 对照运行时 Agent；不得直接再次派发。
   - 如果只剩外部状态，适用时可以考虑下方的长等待方案；否则为每个外部对象准备一次 wait，使用持久化的客户端、会话和有限的总超时启动被动 watcher，确认启动回执并激活 wait，然后结束当前轮次。watcher 拥有该 wait 后，不要再由模型查询同一状态。如果 `check` 报告 `orphaned_wait`，应对同一节点执行 `abort-wait`，再建立新的 wait。
   - 如果 activity 为 `blocked`，报告失败或取消的依赖，以及恢复所需的决定。只有作出该决定后才能使用 `retry`，随后按正常流程调度重置后的节点。
   - 如果继续推进需要用户决定，报告所需的具体决定并停止。
6. `wait` 会准备唯一 watch ID，同时让节点保持 `running`。通过 `waitctl.py start -- ...` 提交 watcher，并使用该命令返回的 state、log、lock 和 startup 绝对路径。启动回执出现后运行 `activate-wait`；只有完成激活，watcher 才能开始查询。`waiting` 节点必须始终有 watcher 持有其 lock；所有权消失时，`check` 报告 `orphaned_wait`，`show` 也返回同名 activity。`wake` 只接受当前活动 ID，并把任何事件对应的节点恢复为 `running`。重新检查一次外部状态，然后完成节点、明确标记失败，或准备下一次 wait。完全相同的重复事件为空操作。如果 prepared 或 active watcher 无法继续，先使用准确的 watch ID 对同一节点运行 `abort-wait`，再建立新 watcher。如果节点已失败，而同一个外部逻辑任务仍需继续，必须 `retry` 原节点；不得新增一个与原后继链断开的替代节点。
7. 结束前，对照原始目标验证结果。如果目标尚未满足，把缺少的工作加入图中并继续；否则使用 `waitctl.py goal -- verify` 记录证据，再运行 `waitctl.py goal -- finish`。

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
- 客户端适配器只能恢复目标初始化时记录的会话 ID。Codex 使用消息队列；CodeWiz、Cursor、Claude Code 和 GitHub Copilot CLI 在子进程中恢复指定会话，并默认只投递一次，避免重复触发模型轮次。

运行 `python ../scripts/waitctl.py --help`、`python ../scripts/wait_goal.py --help` 和 `python ../scripts/wait_for.py --help` 查看命令详情。
