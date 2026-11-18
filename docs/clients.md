# Client delivery

[简体中文](clients.zh-CN.md)

Wait sends events back to the conversation that owns them. A wake message includes the short event ID and full event note. Run `waitctl show SHORT_ID` for the full ID, log path, and associated Goal or Loop state.

| Client | Delivery | Invocation |
| --- | --- | --- |
| Codex | Direct app-server `turn/start` | `$wait`, `$wait loop`, `$wait goal` |
| Claude Code | Native background Bash running `waitctl.py follow` | `/wait`, `/wait loop`, `/wait goal` |

Use `--session ID` for the owning conversation. `--thread` remains an alias. Use `--no-notify` to keep the session visible in the dashboard and retrieve the event manually with `follow`.

## Codex

### Configure the endpoint

Submit with `--client codex --session ID`. Wait records the selected app-server endpoint with the watcher.

| `--remote` | Endpoint | Submission check |
| --- | --- | --- |
| Omitted or `unix://` | `$CODEX_HOME/app-server-control/app-server-control.sock` (`CODEX_HOME` defaults to `~/.codex`) | WebSocket upgrade before the waiting program starts |
| `unix:///absolute/path` | Explicit app-server control socket | Same check |
| `ws://host:port` or `wss://host:port` | Explicit WebSocket server | Recorded as configured, then checked on delivery |

A Unix preflight failure returns `notification_unavailable` with `query_status: not_started`. Use the app-server control socket for local delivery and reserve `~/.codex/ipc/ipc.sock` for Desktop IPC.

For an authenticated endpoint, export the token in the daemon environment and pass `--resume-arg=--remote-auth-token-env=VARIABLE_NAME`. Keep the token value in the environment.

### Delivery and recovery

`waitd` sends one wake event at a time per session ID. Other sessions can receive events in parallel. The adapter reads the thread status, loads it if needed, and starts a turn with the existing model, workspace, and approval settings.

| Delivery state | What happens |
| --- | --- |
| Busy or offline before `turn/start` | Keep the result and next retry time. Resume delivery with the same event ID, including after a daemon restart. |
| `started` | The app-server returned a turn ID. The root agent checks the event and accepts the task result separately. |
| `unconfirmed` | A lost reply, timeout, unexpected helper exit, or interrupted send leaves delivery unconfirmed. Check the original conversation before another wake. |
| Legacy `queued` | The previous adapter accepted the event for delivery. Check the original conversation to see whether it was processed. |

The retry delay grows to at most five minutes. Explicit rejections count toward the default limit of 12 attempts. Busy and offline results remain pending outside that limit. Each Codex attempt has a default timeout of 60 seconds. Before each attempt, Wait checks cancellation and Goal or Loop ownership.

After delivery, the root agent uses Goal `wake` or Loop `begin` to handle the event. A Goal watcher keeps its lock until acknowledgement or the wake acknowledgement deadline. See the [app-server lifecycle](https://learn.chatgpt.com/docs/app-server) for transport details.

### Desktop closed and remote execution

Run `waitd` on the execution host and point it at the owning app-server. Desktop can stay closed while those services are running. If Desktop also stops the app-server, pending events stay on disk and delivery resumes when the same endpoint returns. Approvals and Desktop-only tools can still require the user.

## Claude Code

1. Submit with `--client claude --session ID` and keep the watch ID.
2. Run this command through the owning session's native background-task feature:

   ```bash
   python /absolute/path/src/waitctl.py follow WATCH_ID --timeout 3600
   ```

3. Set a finite timeout that covers the remaining watcher deadline, keep the native task ID, and end the turn.
4. When the task returns, inspect the watcher log and follow the resume steps for Wait, Goal, or Loop.

When you reopen a closed conversation, attach `follow` to the same watcher. A retained completed record returns immediately.

`native_pending` means the event is saved and ready for `follow`. A Goal watch keeps its lock until `wake` acknowledges the event or the notification deadline expires. On expiry, Wait saves `unconfirmed` and retains the event for recovery.
