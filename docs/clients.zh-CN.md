# 客户端投递

[English](clients.md)

Wait 把事件送回所属会话。唤醒消息包含事件短 ID 和完整说明。运行 `waitctl show SHORT_ID` 可查看完整 ID、日志路径和关联的 Goal 或 Loop 状态。

| 客户端 | 投递方式 | 调用 |
| --- | --- | --- |
| Codex | 直接调用 app-server `turn/start` | `$wait`、`$wait loop`、`$wait goal` |
| Claude Code | 原生后台 Bash 执行 `waitctl.py follow` | `/wait`、`/wait loop`、`/wait goal` |

用 `--session ID` 指定所属会话，`--thread` 是兼容别名。使用 `--no-notify` 可在看板保留会话归属，再通过 `follow` 手动领取事件。

## Codex

### 配置端点

使用 `--client codex --session ID` 提交。Wait 会在等待记录中保存所选 app-server 端点。

| `--remote` | 端点 | 提交时检查 |
| --- | --- | --- |
| 省略或使用 `unix://` | `$CODEX_HOME/app-server-control/app-server-control.sock`（`CODEX_HOME` 默认为 `~/.codex`） | 等待程序启动前完成 WebSocket 升级 |
| `unix:///绝对路径` | 指定 app-server 控制套接字 | 同上 |
| `ws://host:port` 或 `wss://host:port` | 指定 WebSocket 服务 | 提交时标记为已配置，投递时检查 |

Unix 端点预检失败会返回 `notification_unavailable` 和 `query_status: not_started`。本机投递使用 app-server 控制套接字。`~/.codex/ipc/ipc.sock` 供 Desktop 内部通信使用。

认证端点的 token 放在守护进程环境中，提交时用 `--resume-arg=--remote-auth-token-env=变量名` 引用。

### 投递与恢复

`waitd` 按会话 ID 串行投递。不同会话可以并行。适配器读取会话状态，按需加载会话，再使用原模型、工作区和审批设置启动一轮。

| 投递状态 | 如何处理 |
| --- | --- |
| 调用 `turn/start` 前遇到会话忙或连接离线 | 保存结果和下次重试时间。即使 `waitd` 重启，也用同一个事件 ID 继续投递。 |
| `started` | app-server 已返回轮次 ID。根 Agent 仍需核对事件并验收结果。 |
| `unconfirmed` | 回执丢失、超时、投递进程异常退出或发送中断时，先检查原会话，再决定是否唤醒。 |
| 旧版 `queued` | 旧适配器已接收待投递事件。到原会话确认是否已处理。 |

重试间隔逐步增加，最长五分钟。明确拒绝默认最多尝试 12 次，忙碌和离线不计入这个上限。每次 Codex 投递默认限时 60 秒。每次尝试前，Wait 检查任务是否取消，以及 Goal 或 Loop 是否仍关联此等待。

投递后，根 Agent 通过 Goal 的 `wake` 或 Loop 的 `begin` 接续事件。Goal 的等待锁保留到确认事件或超过确认期限。传输细节见 [app-server 生命周期](https://learn.chatgpt.com/docs/app-server)。

### 桌面关闭与远程运行

在执行主机上运行 `waitd`，并指向会话所属的 app-server。两项服务运行时，可以关闭 Desktop。如果 Desktop 同时结束 app-server，待投递事件会留在磁盘上，同一端点恢复后继续发送。审批及桌面专属工具仍可能需要用户参与。

## Claude Code

1. 使用 `--client claude --session ID` 提交，保留等待 ID。
2. 在所属会话中，使用原生后台任务功能运行：

   ```bash
   python /绝对路径/src/waitctl.py follow WATCH_ID --timeout 3600
   ```

3. 设置覆盖剩余等待期限的有限超时，保留后台任务 ID，结束本轮。
4. 后台任务返回后，查看等待日志，按 Wait、Goal 或 Loop 的恢复步骤接着做。

重新打开会话后，对原等待执行 `follow`。仍保留的已完成记录会立即返回。

`native_pending` 表示事件已保存，可通过 `follow` 领取。Goal 的等待锁保留到 `wake` 确认或通知期限到期。超时时，Wait 记录 `unconfirmed` 并保留事件供恢复。
