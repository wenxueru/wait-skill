---
name: wait-goal
description: 不使用原生 Goal 模式，运行持久、事件驱动的目标。适用于包含依赖步骤、Agent 或长时间外部等待，并且在没有可执行工作时应停止模型轮次的任务。如果只需监视一个外部状态，请使用 wait。
---

# Wait Goal

将目标作为持久化依赖图运行。不要启动原生 `/goal`；本 Skill 通过显式的 `$wait-goal` 唤醒消息负责续跑。

使用 `scripts/wait_goal.py` 持久化图状态，使用 `scripts/wait_for.py` 轮询外部状态。开始或恢复目标前阅读 [docs/wait-goal.zh-CN.md](docs/wait-goal.zh-CN.md)；节点需要等待命令所报告的外部状态时，阅读 [docs/wait.zh-CN.md](docs/wait.zh-CN.md)。

## 调用方式

- `$wait-goal <目标>`：启动目标。
- `$wait-goal resume <状态文件>`：收到排队的唤醒消息或用户请求后恢复目标。
- `$wait-goal status <状态文件>`：报告持久化状态，但不推进目标。
- `$wait-goal pause|cancel <状态文件>`：执行相应的控制操作。

## 核心行为

1. 在开始实质工作前持久化目标和初始节点。新发现的工作应加入图中成为节点，不要只保留在对话上下文里。
2. 让每个节点尽可能独立：只产生一个有界结果，使用明确且由依赖支持的 `--input`、验收检查和预期产物，并声明 `--read-only` 或具体写入路径。只有不可避免的执行顺序或信息流才使用依赖边；紧密耦合的工作应合并到同一节点。
3. 先运行 `wait_goal.py check`，然后只执行 `ready` 返回的节点。ready frontier 会自动串行化写入范围重叠或未声明范围的节点。只有根 Agent 可以调度工作和修改图。根 Agent 可以并行执行相互独立的节点，但每个子 Agent 只能向根 Agent 汇报；不得联系或等待其他子 Agent，不得修改图或创建 Agent。对于 agent 节点，先持久化 `prepare-agent`，把返回的 dispatch token 写入子任务，再使用 `start --dispatch-token ... --agent-id ...` 关联运行时返回的 ID。
4. 只有节点的验收检查通过且预期产物存在时，才能将其标记为完成。在状态中记录简洁结果和产物，再调度新进入 ready 的节点。
5. 没有 ready 节点时：
   - 如果仍有 Agent 在运行，使用运行时提供的阻塞式 Agent 等待，只在 Agent 完成或需要处理时恢复。如果 activity 为 `dispatching`，采取其他操作前必须用保存的 dispatch token 对照运行时 Agent；不得直接再次派发。
   - 如果只剩外部状态，为每个外部对象准备一次 wait，启动被动 watcher，确认启动回执并激活 wait，然后结束当前轮次。watcher 拥有该 wait 后，不要再由模型查询同一状态。
   - 如果 activity 为 `blocked`，报告失败或取消的依赖，以及恢复所需的决定。只有作出该决定后才能使用 `retry`，随后按正常流程调度重置后的节点。
   - 如果继续推进需要用户决定，报告所需的具体决定并停止。
6. `wait` 会准备唯一 watch ID，同时让节点保持 `running`。启动 `wait_for.py` 时，使用该命令返回的 state、log、lock 和 startup 绝对路径。启动回执出现后运行 `activate-wait`；只有完成激活，watcher 才能开始查询。`wake` 只接受当前活动 ID，并把任何事件对应的节点恢复为 `running`。重新检查一次外部状态，然后完成节点、明确标记失败，或准备下一次 wait。完全相同的重复事件为空操作。如果 prepared 或 active watcher 无法继续，先使用准确的 watch ID 运行 `abort-wait`，再创建替代 watcher。
7. 结束前，对照原始目标验证结果。如果目标尚未满足，把缺少的工作加入图中并继续；否则使用 `wait_goal.py verify` 记录证据，再运行 `wait_goal.py finish`。

## 不变量

- 等待必须由事件驱动。不要为未变化的状态消耗模型轮次，也不要定期发送进度消息。
- 图可以演化，但必须保持无环，并且每个依赖都必须存在。
- 状态修改以原子方式追加到目标事件历史中。执行期间发现新工作时，使用 `--reason` 说明原因。
- 通知只是提示，不是事实证明；采取行动前重新检查当前状态。
- 查询命令必须只读。唤醒不会授予重试、部署、重启或以其他方式修改外部系统的权限。
- 不要只为了提高并发度而 fan-out；子 Agent 会增加 Token 消耗。短小的顺序工作或写入范围重叠的工作应优先由一个 Agent 完成。
- 只有确认 prepared dispatch 没有创建存活的子 Agent，或已终止该 Agent 后，才能运行 `abort-agent`。
- 凭据不得写入 argv、图状态、watcher 日志或通知。

运行 `python scripts/wait_goal.py --help` 和 `python scripts/wait_for.py --help` 查看命令详情。
