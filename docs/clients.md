# Client delivery

[简体中文](clients.zh-CN.md)

`waitd` supports two documented delivery paths. Both use the owning conversation's exact session ID and deliver a service-generated resume instruction containing the log path, event ID, and agent-authored `event_note`.

| Client | Delivery path | Invocation |
| --- | --- | --- |
| Codex | Direct app-server `turn/start` | `$wait`, `$wait loop`, `$wait goal` |
| Claude Code | Native background Bash running `waitctl.py follow` | `/wait`, `/wait loop`, `/wait goal` |

`--thread` is a compatibility alias for `--session`. `--remote` applies only to Codex. Credentials must not appear in session IDs, arguments, notes, or persisted files.

## Codex

### Endpoint selection

Submit with `--client codex --session ID`. Add `--remote ENDPOINT` when the default app-server control socket is not available.

Accepted endpoint forms are:

- `unix://` or no `--remote`: `$CODEX_HOME/app-server-control/app-server-control.sock`, with `$CODEX_HOME` defaulting to `~/.codex`;
- `unix:///absolute/path`: an explicit Unix app-server control socket;
- `ws://host:port` or `wss://host:port`: an explicit WebSocket endpoint.

The Desktop socket at `~/.codex/ipc/ipc.sock` is not an app-server endpoint and is never selected automatically.

### Submission and preflight

Before starting the waiting program, `waitd` resolves the endpoint and checks the notification channel:

- A Unix endpoint must exist and complete a WebSocket upgrade. Success is reported as `notification_channel.status: ready`.
- A `ws://` or `wss://` endpoint is reported as `configured`; URL validation and connectivity are checked at delivery.
- An unavailable or incompatible Unix endpoint returns `notification_unavailable` with `query_status: not_started`. No watcher is created and no retry begins.
- A wait without a session has notification disabled and can still be followed manually.

This separates query startup from notification readiness, so a healthy status query cannot hide a broken wake path.

### Delivery and recovery

`waitd` runs `src/wait_codex.py` against the same endpoint and exact thread ID. The helper initializes a JSON-RPC connection, reads runtime thread status, resumes only an unloaded thread, and calls `turn/start` with the generated `$wait resume ...`, `$wait goal resume ...`, or `$wait loop resume ...` instruction. It preserves the thread's model, working directory, instructions, sandbox, and approval settings. It never spawns a second Codex executor or waits for a Desktop queue consumer.

- `notification: started` means `turn/start` returned an actual turn ID. The helper's JSON stdout records that ID. Starting a turn is not business success; the root still validates the event and result.
- An active thread returns `busy` without steering it. The service retries after a bounded delay; a server-side race can still cause `turn/start` to reach a turn that became active after the status read.
- A connection failure before `turn/start` retries as `notification_unavailable`; explicit server rejections also use the bounded retry policy. Defaults are 12 attempts and 60 seconds per attempt, with exponential backoff capped at 300 seconds.
- A lost `turn/start` acknowledgement or delivery timeout is `unconfirmed` and is never automatically replayed. A daemon crash during `attempting` also recovers as `unconfirmed`. Inspect the original thread before recovery.
- Watcher cancellation and goal/loop ownership are rechecked before each attempt. The same event ID is preserved throughout retries.

For an authenticated WS/WSS endpoint, export a token in the daemon environment and pass `--resume-arg=--remote-auth-token-env=VARIABLE_NAME` on the watcher. No token value goes into argv or state. Codex resume arguments cannot override thread settings. The helper verifies WSS certificates; Unix and TCP WebSockets validate the upgrade response. The selected endpoint is saved in watcher argv at submission.

### Desktop closed and remote execution

Run `waitd` on the host that owns the conversation, using its app-server endpoint and thread ID. If that remote app-server remains alive when the Desktop app closes, the wake starts directly on the remote server. Reopening Desktop is unnecessary for execution. An app-server stopped with the app, an offline execution host, expired credentials, required user approval, or Desktop-only tools can still block progress; this skill does not start a replacement server or bypass interaction requirements.

A previously recorded `notification: queued` came from the old queue adapter. It proves only queue acceptance and is retained as legacy state; do not blindly replay it during migration. Reconcile the original conversation and event ID first. Existing goal/loop JSON state paths remain valid. After delivery, goal `wake` and loop `begin` still run in the resumed root; goal watchers retain ownership until acknowledgement or the configured wake-ack deadline.

The protocol follows the [official app-server lifecycle](https://learn.chatgpt.com/docs/app-server).

## Claude Code

Claude Code does not use a headless `claude --resume` process to notify an open conversation. The owning interactive session attaches a native background Bash task to the watcher instead.

1. Submit the watcher with `--client claude --session ID` and keep its watch ID.
2. In the owning root session, start this command with the client's native background-task feature:

   ```bash
   python /absolute/path/src/waitctl.py follow WATCH_ID --timeout 3600
   ```

3. Choose a finite follow timeout that covers the remaining watcher deadline, retain the native task ID, and end the turn.
4. When the background task completes, read its output and watcher log, validate the event, and apply the normal wait, goal, or loop resume protocol.

Shell `&`, tmux, and a separate headless Claude process do not provide the owning conversation's native completion notification. Closing the conversation stops its background task; after reopening, attach `follow` to the original watch. A retained completed record returns immediately, so a replacement watcher is unnecessary.

`notification: native_pending` means the event is durable and available to `follow`; it does not mean the root accepted it. Goal waits remain owned until `wake` or the notification deadline. If that deadline passes, the watcher records `unconfirmed` and preserves the event for recovery.
