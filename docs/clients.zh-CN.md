# 客户端适配

[English](clients.md)

依赖图和 watcher 与客户端无关。通过 `waitctl.py start --` 提交的 watcher 参数使用 `--client CLIENT --session ID` 选择最后的投递方式：

| 客户端 | 恢复命令 | Skill 调用 |
| --- | --- | --- |
| CodeWiz | `codewiz run --session ID MESSAGE` | `/wait`、`/wait-loop`、`/wait-goal` |
| Cursor CLI | `cursor-agent --print --resume=ID MESSAGE` | `/wait`、`/wait-loop`、`/wait-goal` |
| Claude Code | 原生后台 Bash 执行 `waitctl.py follow WATCH_ID --timeout SECONDS` | `/wait`、`/wait-loop`、`/wait-goal` |
| GitHub Copilot CLI | `copilot --resume=ID --prompt MESSAGE` | `/wait`、`/wait-loop`、`/wait-goal` |
| Codex | `codex queue --remote ENDPOINT --thread ID --message MESSAGE` | `$wait`、`$wait-loop`、`$wait-goal` |

传入持有该 wait 的准确会话 ID。`--thread` 保留为 `--session` 的兼容别名；`--remote` 只供 Codex 使用。

Codex 会把消息加入队列并快速返回，因此投递失败默认最多重试 12 次，单次超时 60 秒。CodeWiz、Cursor 和 Copilot 会恢复 CLI 会话并等待该轮次返回，默认只尝试一次、单次超时一小时，因为超时结果不明确——目标轮次可能已经运行。只有客户端能证明失败尝试没有启动轮次时，才覆盖 `--max-notification-attempts`。

恢复命令默认沿用客户端自身的权限策略。恢复后的轮次若需要非交互模式默认不授予的权限，应在用户授权范围内使用 `--resume-arg=值` 逐项显式传入；否则应要求用户手动恢复。

CLI 恢复命令运行在独立进程中，不能证明已有交互界面收到消息；成功结果记为 `notification: completed`，Codex 队列接收记为 `queued`。

所有客户端恢复后，都由 Root 读取消息引用的日志、对照持久状态校验事件，再执行 [wait](wait.zh-CN.md)、[goal](wait-goal.zh-CN.md) 或 [loop](wait-loop.zh-CN.md) 的恢复协议。投递状态本身不代表任务完成。

## CodeWiz

使用 `--client codewiz --session ID` 提交，绑定所属 CodeWiz 会话 ID。事件就绪时，服务执行 `codewiz run --session ID MESSAGE`，无需挂接 `follow`。恢复的 CLI 轮次接收消息模板中的 `/wait` 恢复指令。

确保服务环境中的 `codewiz` 已完成认证。投递超时后，先检查保存的会话和 watcher 日志再重试：恢复轮次可能已经执行过操作。此适配器未实现向已打开界面单独投递通知。

## Cursor CLI

使用 `--client cursor --session ID` 提交，绑定所属 Cursor CLI 会话 ID。服务执行 `cursor-agent --print --resume=ID MESSAGE`，在该无头轮次中执行 `/wait` 恢复指令，无需挂接 `follow`。

已获授权的任务若需在 headless 模式应用修改，仅在环境受到适当限制时使用 `--resume-arg=--force`，因为它会绕过交互确认；否则需要权限时手动恢复。CLI 进程成功返回不代表消息已进入打开的 Cursor 编辑器对话。

## Claude Code

使用 `--client claude`。`claude --print --resume` 运行无头轮次，不会向已有对话投递通知，适配器不再调用它。

1. 正常提交 watcher，保留 `watch_id`。goal 先完成启动与激活握手；loop 在 `complete` 后使用状态中保存的 `watch_id`。
2. 在**所属根 Agent 的交互会话**中，以 Bash 的 `run_in_background: true` 执行 `python /绝对路径/scripts/waitctl.py follow WATCH_ID --timeout SECONDS`。设置覆盖 watcher 剩余超时的有限上限，保留原生任务 ID，然后结束轮次。shell `&`、tmux 或另一个无头 Claude 进程不能代替原生后台工具。
3. 该命令阻塞等待服务事件，不轮询。结果持久化后，输出事件、日志路径与恢复消息并退出，由 Claude 的原生任务完成通知送回所属对话。读取输出和日志、校验事件，再执行 wait／goal／loop 的恢复协议。

`notification: native_pending` 只表示事件可由原生任务读取，不代表 Claude 已消费。goal 的 `wake`、loop 的 `begin` 仍由根 Agent 持久确认。原生任务超时、连接失败或被取消不代表外部对象成功：先检查同一个已保存的 watch，再决定恢复。关闭 Claude 会话会结束原生任务；重新打开后对原 watch 重新挂接 `follow`，不要另建 watcher。仍保留的已完成记录会立即返回。没有原生后台任务能力时，无法自动唤醒交互会话。

此路径不需要 MCP 配置或 Channels。

goal 等待期间，服务保留所有权直到 Root 执行 `wake`，上限由 `--notification-timeout` 指定（默认从事件可用起 3600 秒）。原生投递不使用投递成功后的 `--wake-ack-timeout`。未收到 Root 确认时，watcher 以 `notification: unconfirmed` 失败，日志保留外部事件。恢复时检查日志和 goal；若节点已孤立，按 goal 恢复协议处理原节点，不要创建替代节点。重新挂接 `follow` 不会延长截止时间。

## GitHub Copilot CLI

使用 `--client copilot --session ID` 提交，绑定所属 Copilot CLI 会话 ID。服务执行 `copilot --resume=ID --prompt MESSAGE`，把 `/wait` 恢复指令交给恢复的 CLI 轮次，无需挂接 `follow`。

适配器不自动批准权限。通过 `--resume-arg` 配置恢复任务已获授权的权限；需要交互确认时手动恢复。超时结果不明确时，先检查保存的会话再重试。此路径面向 Copilot CLI，不是 IDE 聊天面板。

## Codex

使用 `--client codex --session ID --remote ENDPOINT` 提交，绑定所属 Root 的线程 ID 和队列端点。服务执行 `codex queue --remote ENDPOINT --thread ID --message MESSAGE`。消息模板使用 `$wait resume ...`，无需原生后台 `follow` 任务。

已安装的 `codex` 命令必须支持 `queue`，并能访问该端点。`notification: queued` 只确认队列接收，不代表 Root 已执行消息；goal 的 `wake` 和 loop 的 `begin` 仍在恢复的 Root 中执行。队列投递失败时，先检查记录的投递结果和端点连通性，再决定恢复操作。

## 进度工具

规划时，各 Agent 按运行时 schema 使用可用的原生 Todo 或计划工具。共享列表由所属根 Agent 更新；子 Agent 有独立列表时使用，否则汇报进度。保留其他任务条目。

执行协议规定同步时机：持久状态转换、watcher 结果核实和最终验收之后。goal 条目用节点 ID 标识，独立等待用日志路径标识；恢复时复用相同标识。工具不可用或失败时依据持久状态继续，在后续可用的执行轮次同步。

| 执行状态 | 原生进度条目 |
| --- | --- |
| 待处理或阻塞 | 未完成，说明未满足的依赖 |
| 派发中或运行中 | 进行中，保留 dispatch／节点 ID |
| 等待 | 支持时使用等待状态；否则保持未完成并注明条件和截止时间 |
| 验收完成 | 完成 |
| 失败、取消或超时 | 对应状态或明确的结果标签，与成功区分 |

图较大时按阶段分组，在说明中保留节点 ID。工具只允许一个进行中条目时，用执行阶段条目列出并行节点。

## 初始化目标

在目标中持久化客户端和会话：

```bash
python scripts/waitctl.py goal -- init \
  --objective "CI 通过后发布" \
  --client claude \
  --session "$AGENT_SESSION_ID"
```

不传 `--state` 时，`init` 会返回 `/tmp/.wait-goal/` 下按项目隔离的唯一路径；后续 goal 命令使用该 `state_file`。

启动 watcher 时使用相同值。Codex 以外的客户端使用斜杠恢复指令：

```bash
python scripts/waitctl.py start -- \
  --client claude \
  --session "$AGENT_SESSION_ID" \
  --label "CI" \
  --ready success \
  --timeout 3600 \
  --lock-file /tmp/wait-ci.lock \
  --log-file /tmp/wait-ci.json \
  --message-template '/wait resume /tmp/wait-ci.json; event_id={event_id}; event={event}; status={status}' \
  -- ci status --output state
```

目标所属的 watcher 还必须传入准备阶段生成的 watch ID，以及 [wait.zh-CN.md](wait.zh-CN.md) 说明的 `--goal-state`、`--goal-node` 和 `--startup-file` 握手参数。

## 要求

- CLI 投递要求客户端已安装、认证并位于 watcher 的 `PATH` 中；Claude 原生投递要求所属交互会话提供后台 Bash 工具。
- 保存的会话可以恢复，并能访问目标状态、watcher 日志和工作区。
- 不要在会话 ID、消息模板、查询 argv 或持久化文件中传递凭据。
