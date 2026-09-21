# 客户端投递

[English](clients.md)

`waitd` 只文档化两种投递方式。两者都绑定所属对话的准确会话 ID，并投递由服务生成的恢复指令；指令包含日志路径、event ID 和 Agent 编写的 `event_note`。

| 客户端 | 投递方式 | 调用 |
| --- | --- | --- |
| Codex | 通过 app-server 端点执行 `codex queue` | `$wait`、`$wait-loop`、`$wait-goal` |
| Claude Code | 原生后台 Bash 执行 `waitctl.py follow` | `/wait`、`/wait-loop`、`/wait-goal` |

`--thread` 是 `--session` 的兼容别名；`--remote` 只用于 Codex。会话 ID、参数、note 和持久化文件都不得包含凭据。

## Codex

### 选择端点

使用 `--client codex --session ID` 提交。默认 app-server 控制 socket 不可用时，再传 `--remote ENDPOINT`。

支持以下端点：

- `unix://` 或省略 `--remote`：使用 `$CODEX_HOME/app-server-control/app-server-control.sock`，`$CODEX_HOME` 默认是 `~/.codex`；
- `unix:///绝对路径`：显式指定 Unix app-server 控制 socket；
- `ws://host:port` 或 `wss://host:port`：显式指定 WebSocket 端点。

Codex Desktop 创建的 `~/.codex/ipc/ipc.sock` 不是 app-server 端点，服务不会自动使用它。

### 提交与预检

等待程序启动前，`waitd` 先解析端点并检查通知通道：

- Unix 端点必须存在并完成 WebSocket 升级；成功时返回 `notification_channel.status: ready`。
- `ws://` 和 `wss://` 端点只校验格式，返回 `configured`；实际连通性由投递确认。
- Unix 端点缺失或不兼容时，立即返回 `notification_unavailable` 和 `query_status: not_started`，不创建 watcher，也不开始重试。
- 不绑定会话的 wait 会把通知标为 `disabled`，仍可手动执行 `follow`。

这样查询启动状态和通知通道状态彼此独立，不会用正常的状态查询掩盖失效的唤醒路径。

### 投递与恢复

等待程序结束后，服务执行：

```text
codex queue --remote ENDPOINT --thread ID --message MESSAGE
```

`MESSAGE` 是自动生成的 `$wait resume ...` 指令。队列命令的 stdout 和 stderr 都写入 watcher 日志，每种最多保留 64 KiB。

- 退出码为零时记录 `notification: queued`；这只表示队列接收，不代表 Root 已处理事件。
- 连接、socket 或 WebSocket 故障记录 `notification: notification_unavailable`，并停止重试。
- 其他队列拒绝按有界策略重试：默认最多 12 次，每次 60 秒。
- 投递超时记录 `unconfirmed`；重试前必须检查目标任务，因为消息可能已经被接收。

投递后，goal 的 `wake` 和 loop 的 `begin` 仍由恢复后的 Root 执行。goal wait 会保留所有权，直到 Root 确认或超过 wake-ack 截止时间。

## Claude Code

Claude Code 不通过无头 `claude --resume` 进程通知已有对话，而是在所属交互会话中把原生后台 Bash 任务挂到 watcher 上。

1. 使用 `--client claude --session ID` 提交 watcher，并保存 watch ID。
2. 在所属 Root 会话中，用客户端原生后台任务功能启动：

   ```bash
   python /绝对路径/src/waitctl.py follow WATCH_ID --timeout SECONDS
   ```

3. follow 超时必须有限，并覆盖 watcher 的剩余期限；保存原生任务 ID，然后结束本轮。
4. 后台任务完成后，读取它的输出和 watcher 日志，校验事件，再执行 wait、goal 或 loop 的恢复协议。

Shell `&`、tmux 和独立的无头 Claude 进程都不能向所属对话提供原生完成通知。关闭会话会结束后台任务；重新打开后，对原 watcher 再执行 `follow`。已完成且仍保留的记录会立即返回，无需创建替代 watcher。

`notification: native_pending` 表示事件已经持久化并可由 `follow` 读取，不代表 Root 已接受。goal wait 会保持所有权直到执行 `wake` 或超过通知截止时间；超时后记录 `unconfirmed` 并保留事件供恢复。
