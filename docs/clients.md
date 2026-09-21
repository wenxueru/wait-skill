# Client delivery

[简体中文](clients.zh-CN.md)

`waitd` supports two documented delivery paths. Both use the owning conversation's exact session ID and deliver a service-generated resume instruction containing the log path, event ID, and agent-authored `event_note`.

| Client | Delivery path | Invocation |
| --- | --- | --- |
| Codex | `codex queue` to an app-server endpoint | `$wait`, `$wait-loop`, `$wait-goal` |
| Claude Code | Native background Bash running `waitctl.py follow` | `/wait`, `/wait-loop`, `/wait-goal` |

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
- A `ws://` or `wss://` endpoint is syntax-checked and reported as `configured`; delivery is the connectivity check.
- An unavailable or incompatible Unix endpoint returns `notification_unavailable` with `query_status: not_started`. No watcher is created and no retry begins.
- A wait without a session has notification disabled and can still be followed manually.

This separates query startup from notification readiness, so a healthy status query cannot hide a broken wake path.

### Delivery and recovery

When the waiting program finishes, the service runs:

```text
codex queue --remote ENDPOINT --thread ID --message MESSAGE
```

`MESSAGE` is the generated `$wait resume ...` instruction. Queue stdout and stderr are saved in the watcher log, each capped at 64 KiB.

- Exit code zero records `notification: queued`. This confirms queue acceptance, not that the root handled the event.
- Connection, socket, or WebSocket failures record `notification: notification_unavailable` and stop retrying.
- Other queue rejections follow the bounded notification retry policy: 12 attempts by default, 60 seconds per attempt.
- A delivery timeout is `unconfirmed`; inspect the target task before retrying because the message may already have been accepted.

After delivery, goal `wake` and loop `begin` still run in the resumed root. For goal waits, the service retains ownership until root acknowledgement or the configured wake-ack deadline.

## Claude Code

Claude Code does not use a headless `claude --resume` process to notify an open conversation. The owning interactive session attaches a native background Bash task to the watcher instead.

1. Submit the watcher with `--client claude --session ID` and keep its watch ID.
2. In the owning root session, start this command with the client's native background-task feature:

   ```bash
   python /absolute/path/src/waitctl.py follow WATCH_ID --timeout SECONDS
   ```

3. Choose a finite follow timeout that covers the remaining watcher deadline, retain the native task ID, and end the turn.
4. When the background task completes, read its output and watcher log, validate the event, and apply the normal wait, goal, or loop resume protocol.

Shell `&`, tmux, and a separate headless Claude process do not provide the owning conversation's native completion notification. Closing the conversation stops its background task; after reopening, attach `follow` to the original watch. A retained completed record returns immediately, so a replacement watcher is unnecessary.

`notification: native_pending` means the event is durable and available to `follow`; it does not mean the root accepted it. Goal waits remain owned until `wake` or the notification deadline. If that deadline passes, the watcher records `unconfirmed` and preserves the event for recovery.
