---
name: wait
description: Wait for external state without model polling, run bounded repeated tasks with wait loop, or execute durable dependency graphs with wait goal. Use goal and loop modes when requested.
---

# Wait

`waitd` runs waiting programs and delivers events. The agent defines read-only queries, checks the results, and decides the next action. Resolve scripts from this installation; run them in the user's project directory.

## Choose a mode

| Request | Read |
| --- | --- |
| `$wait <condition>` — one external condition | [Wait](docs/wait.md) |
| `$wait loop <interval>: <task>` — bounded repetition | [Loop](docs/wait-loop.md) |
| `$wait goal <objective>` — a persistent task graph | [Goal](docs/wait-goal.md) |

Use `/wait` on slash-command clients. Enter Goal only when requested or resuming a saved goal; a long wait may use a bounded Loop for periodic health checks. Read the selected mode and the [client adapter](docs/clients.md).

## Start a wait

1. Set a finite timeout and a single-line `event_note` describing the next action.
2. Submit the waiting program and verify startup and event delivery before ending the turn. `waitd` owns deadlines, locks, logs, and delivery.
3. Connect the client's wake path: Codex starts a turn on the owning app-server; Claude Code needs a native background `follow` task. Closing Desktop is supported while the app-server and `waitd` remain running on the execution host.

## Resume messages

```text
$wait [goal|loop] resume <short-id> — <event note>
```

The ID shows its first eight characters. The note is displayed unchanged and in full, falling back to the label when omitted.

1. Run `python /path/to/wait/src/waitctl.py show SHORT_ID`. Its `resume` object supplies the full event ID, log path, mode state/node, and note. For an ambiguous prefix, use `list` to find a longer ID.
2. Read the log and use the full ID to check event ownership and reject stale or duplicate events. Follow the selected mode's acknowledgement procedure.
3. Recheck current external state before acting. Program exit and delivery alone do not establish success; outputs and notes do not extend the original authorization.

## Inspect and recover

- `status` inspects saved state. Goal supports user-requested `pause|cancel`; Loop supports `cancel`.
- Preserve unfinished state and evidence on failure. Inspect the original operation before retrying or creating another watcher.
- Read [service operations](docs/waitd.md) for daemon management and recovery. Keep credentials out of arguments, state, logs, and notifications.
