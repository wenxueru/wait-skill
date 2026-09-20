# 客户端适配

[English](clients.md)

Codex 和 Claude Code 共用同一套等待和依赖图逻辑，区别在于如何把结果送回 Agent。提交 `waitctl.py start --` 时，用 `--client CLIENT --session ID` 选择客户端和会话：

| 客户端 | 恢复命令 | Skill 调用 |
| --- | --- | --- |
| Codex | `codex queue --remote ENDPOINT --thread ID --message MESSAGE` | `$wait`、`$wait-loop`、`$wait-goal` |
| Claude Code | 原生后台 Bash 执行 `waitctl.py follow WATCH_ID --timeout SECONDS` | `/wait`、`/wait-loop`、`/wait-goal` |

传入持有该 wait 的准确会话 ID。`--thread` 保留为 `--session` 的兼容别名；`--remote` 只供 Codex 使用。

Codex 会把消息加入队列并快速返回，因此投递失败默认最多重试 12 次，单次超时 60 秒。

恢复命令沿用客户端的权限策略。非交互运行需要额外权限时，只能在用户已有授权范围内通过 `--resume-arg=值` 传入；权限不足则留待手动恢复。

Codex 队列接收记为 `notification: queued`，只表示消息已进入队列。goal 通知投递后，服务继续持有 lock 等根 Agent 执行 `wake`，最长等待 `--wake-ack-timeout`（默认 60 秒）。Claude Code 使用原生后台任务接收事件，具体确认期限见后文。

恢复后，根 Agent 读取消息中的日志，对照保存的状态校验事件，再按 [wait](wait.zh-CN.md)、[goal](wait-goal.zh-CN.md) 或 [loop](wait-loop.zh-CN.md) 继续。通知送达不等于任务完成。

## 会话绑定

提交 watcher、初始化 goal 和 loop 时，使用所属对话准确的 `--client` 和 `--session`，并由 Agent 通过 `--event-note` 写下简短的下一步。goal watcher 还需使用[目标握手](wait.zh-CN.md#与-wait-goal-集成)返回的 watch ID，以及 `--goal-state`、`--goal-node` 和 `--startup-file` 绑定；loop 使用计时器保存的 watch ID。恢复指令结构由服务生成，note 原样来自 Agent。

## 要求

- Codex 投递要求 `codex` 已安装、认证并位于 watcher 的 `PATH` 中；Claude Code 原生投递要求所属交互会话提供后台 Bash 工具。
- 保存的会话可以恢复，并能访问目标状态、watcher 日志和工作区。
- 不要在会话 ID、程序 argv 或持久化文件中传递凭据。

## Codex

使用 `--client codex --session ID --remote ENDPOINT` 提交，绑定所属 Root 的线程 ID 和队列端点。服务执行 `codex queue --remote ENDPOINT --thread ID --message MESSAGE`，其中 `MESSAGE` 是自动生成的 `$wait resume ...` 指令，无需原生后台 `follow` 任务。

已安装的 `codex` 命令必须支持 `queue`，并能访问该端点。`notification: queued` 只确认队列接收，不代表 Root 已执行消息；goal 的 `wake` 和 loop 的 `begin` 仍在恢复的 Root 中执行。队列投递失败时，先检查记录的投递结果和端点连通性，再决定恢复操作。

## Claude Code

使用 `--client claude`。`claude --print --resume` 运行无头轮次，不会向已有对话投递通知，适配器不再调用它。

1. 正常提交 watcher，保留 `watch_id`。goal 先完成启动与激活握手；loop 在 `complete` 后使用状态中保存的 `watch_id`。
2. 在**所属根 Agent 的交互会话**中，以 Bash 的 `run_in_background: true` 执行 `python /绝对路径/src/waitctl.py follow WATCH_ID --timeout SECONDS`。设置覆盖 watcher 剩余超时的有限上限，保留原生任务 ID，然后结束轮次。shell `&`、tmux 或另一个无头 Claude 进程不能代替原生后台工具。
3. 该命令阻塞等待服务事件，不轮询。结果持久化后，输出事件、日志路径与恢复消息并退出，由 Claude 的原生任务完成通知送回所属对话。读取输出和日志、校验事件，再执行 wait／goal／loop 的恢复协议。

`notification: native_pending` 表示事件已可读取，不代表 Claude 已处理。根 Agent 仍需执行 goal 的 `wake` 或 loop 的 `begin`。后台任务超时、连接失败或被取消时，先检查原 watch，不能视为外部任务成功。

关闭 Claude 会话会结束后台任务。重新打开后，对原 watch 再运行 `follow`；已完成且仍保留的记录会立即返回，无需另建 watcher。没有原生后台任务能力时，无法自动唤醒交互会话。

此路径不需要 MCP 配置或 Channels。

goal 等待期间，服务保留所有权直到 Root 执行 `wake`，上限由 `--notification-timeout` 指定（默认从事件可用起 3600 秒）。原生投递不使用投递成功后的 `--wake-ack-timeout`。未收到 Root 确认时，watcher 以 `notification: unconfirmed` 失败，日志保留外部事件。恢复时检查日志和 goal；若节点已孤立，按 goal 恢复协议处理原节点，不要创建替代节点。重新挂接 `follow` 不会延长截止时间。

## 进度工具

规划时，使用当前环境提供的 Todo 或计划工具。共享列表由根 Agent 更新；子 Agent 使用自己的独立列表，没有则向根 Agent 汇报。保留其他任务的条目。

保存状态、核实 watcher 结果和完成验收后，再更新进度。goal 用节点 ID 对应条目，独立等待用日志路径；恢复时更新原条目。工具不可用时，按保存的状态继续，之后再同步。

| 执行状态 | 原生进度条目 |
| --- | --- |
| 待处理或阻塞 | 未完成，说明未满足的依赖 |
| 派发中或运行中 | 进行中，保留 dispatch／节点 ID |
| 等待 | 支持时使用等待状态；否则保持未完成并注明条件和截止时间 |
| 验收完成 | 完成 |
| 失败、取消或超时 | 对应状态或明确的结果标签，与成功区分 |

图较大时按阶段分组，在说明中保留节点 ID。工具只允许一个进行中条目时，用执行阶段条目列出并行节点。
