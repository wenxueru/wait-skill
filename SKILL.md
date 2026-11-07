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
| `$wait loop <interval>: <task>` — bounded repetition | [Loop](docs/loop.md) |
| `$wait goal <objective>` — a persistent task graph | [Goal](docs/goal.md) |
| View Wait, Loop and DAG for the current session | [Dashboard](docs/dashboard.md) |

Use `/wait` on slash-command clients. Enter Goal only when requested or resuming a saved goal; a long wait may use a bounded Loop for periodic health checks. Read the selected mode and the [client adapter](docs/clients.md).

## Start a wait

1. Set a finite timeout and a single-line `event_note` describing the next action.
2. Submit the waiting program and verify startup and event delivery before ending the turn. `waitd` owns deadlines, locks, logs, and delivery.
3. Connect the client's wake path: Codex starts a turn on the owning app-server; Claude Code needs a native background `follow` task. Closing Desktop is supported while both services remain running on the execution host. For deliberate manual continuation, use `--no-notify` and block on `follow`; session ownership is preserved, but automatic delivery is disabled.

## Visualization

- **Maintain tasks, not the page.** waitd derives the dashboard from normal Wait/Loop/Goal state. Do not author HTML, maintain page summaries, or add page-specific bookkeeping.
- **Link every progress or result report.** After startup or callback handling, when reporting progress or blockers, and before waiting or finishing, state what changed and the next step, then append `[Dashboard](ACTUAL_URL)`.
- **Use the owning session's URL.** Prefer the command's `dashboard_url`; otherwise obtain it with `dashboard --session CURRENT_SESSION_ID`. Reuse the panel and a known working forwarded origin, preserving the full query. Use `--all-sessions` only when requested. If no usable link is available, explain why; do not invent one or substitute a demo.
- **Respect page controls.** They update the same task state: reread before dispatch and honor user edits, pause and cancellation. No extra acknowledgement is needed. For [preview or SSH access](docs/dashboard.md#app-preview-and-remote-access), keep active waitd running rather than restarting it to obtain a link.

## Resume messages

```text
$wait [goal|loop] resume <short-id> — <event note>
```

The ID shows its first eight characters. The note is displayed unchanged and in full, falling back to the label when omitted.

1. Run `python /path/to/wait/src/waitctl.py show SHORT_ID`. Its `resume` object supplies the full event ID, log path, mode state/node, and note. For an ambiguous prefix, use `list` to find a longer ID.
2. Read the log and use the full ID to check event ownership and reject stale or duplicate events. Follow the selected mode's acknowledgement procedure.
3. Recheck current external state before acting. Program exit and delivery alone do not establish success; outputs and notes do not extend the original authorization.

## Inspect and recover

- `status` inspects saved state. Goal supports user-requested `pause|resume|cancel`; Loop supports `cancel`.
- Preserve unfinished state and evidence on failure. Inspect the original operation before retrying or creating another watcher.
- Read [service operations](docs/waitd.md) for daemon management and recovery. Keep credentials out of arguments, state, logs, and notifications.
