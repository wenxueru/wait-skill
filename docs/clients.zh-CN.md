# 客户端投递

[English](clients.md)

`waitd` 只文档化两种投递方式。两者都绑定所属对话的准确会话 ID，并投递由服务生成的恢复指令；指令包含日志路径、event ID 和 Agent 编写的 `event_note`。

| 客户端 | 投递方式 | 调用 |
| --- | --- | --- |
| Codex | 直接调用 app-server `turn/start` | `$wait`、`$wait loop`、`$wait goal` |
| Claude Code | 原生后台 Bash 执行 `waitctl.py follow` | `/wait`、`/wait loop`、`/wait goal` |

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
- `ws://` 和 `wss://` 端点返回 `configured`；URL 格式与连通性在投递时检查。
- Unix 端点缺失或不兼容时，立即返回 `notification_unavailable` 和 `query_status: not_started`，不创建 watcher，也不开始重试。
- 不绑定会话的 wait 会把通知标为 `disabled`，仍可手动执行 `follow`。

这样查询启动状态和通知通道状态彼此独立，不会用正常的状态查询掩盖失效的唤醒路径。

### 投递与恢复

`waitd` 运行 `src/wait_codex.py`，连接原端点和原会话 ID：先初始化 JSON-RPC 连接、读取线程运行状态，仅在线程未加载时执行 `thread/resume`，再通过 `turn/start` 发送 `$wait resume ...`、`$wait goal resume ...` 或 `$wait loop resume ...`。保持原会话的模型、工作目录、指令、沙箱和审批设置；不启动第二个 Codex 执行器，也不依赖桌面客户端消费队列。

- `notification: started` 表示 `turn/start` 已返回真实 turn ID，记录在 helper 的 JSON stdout 中。启动执行不代表业务成功，Root 仍须核对事件与结果。
- 会话活跃时返回 `busy`，不主动 steer 正在进行的轮次；服务有界延迟重试。状态读取与 `turn/start` 之间存在竞争窗口，线程在窗口内变为活跃时仍可能接收该输入。
- 在发送 `turn/start` 前连接失败时，以 `notification_unavailable` 有界重试；服务明确拒绝请求时也采用有界重试。默认最多 12 次、每次 60 秒，指数退避上限 300 秒。
- `turn/start` 回执丢失或投递超时记录 `unconfirmed`，不自动重放；服务在 `attempting` 阶段崩溃后也按不确定投递恢复。恢复前检查原会话。
- 每次尝试前重新校验取消状态及 goal/loop 所有权；重试始终复用原 event ID。

认证 WS/WSS 端点可在 daemon 环境中导出 token，并给 watcher 传 `--resume-arg=--remote-auth-token-env=变量名`。参数和状态中不写 token 值。Codex 的 resume 参数不可覆盖原会话设置。WSS 验证证书，Unix/TCP WebSocket 验证升级响应；提交时选定的端点保存在 watcher argv 中。

### 桌面关闭与远程运行

`waitd` 应运行在会话所在主机，使用该主机的 app-server 端点和会话 ID。只要远程 app-server 在桌面 App 关闭后仍存活，事件就直接在远程启动执行，无需重开 App。如果 app-server 随 App 一起退出、执行主机离线、凭据失效，或任务需要人工审批、桌面专有工具，进度仍可能受阻；本 Skill 不启动替代服务，也不绕过交互要求。

旧版记录的 `notification: queued` 只表示队列接收；迁移时保留为历史状态，不盲目重发。先核对原会话和 event ID。已有 goal/loop JSON 路径仍然有效。唤醒后仍由 Root 执行 goal `wake` 或 loop `begin`；goal watcher 保留所有权直到确认或超过配置的确认时限。

协议依据[官方 app-server 生命周期文档](https://learn.chatgpt.com/docs/app-server)。

## Claude Code

Claude Code 不通过无头 `claude --resume` 进程通知已有对话，而是在所属交互会话中把原生后台 Bash 任务挂到 watcher 上。

1. 使用 `--client claude --session ID` 提交 watcher，并保存 watch ID。
2. 在所属 Root 会话中，用客户端原生后台任务功能启动：

   ```bash
   python /绝对路径/src/waitctl.py follow WATCH_ID --timeout 3600
   ```

3. follow 超时必须有限，并覆盖 watcher 的剩余期限；保存原生任务 ID，然后结束本轮。
4. 后台任务完成后，读取它的输出和 watcher 日志，校验事件，再执行 wait、goal 或 loop 的恢复协议。

Shell `&`、tmux 和独立的无头 Claude 进程都不能向所属对话提供原生完成通知。关闭会话会结束后台任务；重新打开后，对原 watcher 再执行 `follow`。已完成且仍保留的记录会立即返回，无需创建替代 watcher。

`notification: native_pending` 表示事件已经持久化并可由 `follow` 读取，不代表 Root 已接受。goal wait 会保持所有权直到执行 `wake` 或超过通知截止时间；超时后记录 `unconfirmed` 并保留事件供恢复。
