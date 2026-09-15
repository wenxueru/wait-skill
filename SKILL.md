---
name: wait
description: Passively wait for one external command-reported state across CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex without spending model turns polling.
---

# Wait

Move repeated checks for one external object into the local `waitd` supervisor, then end the model turn until its event resumes the session. Read [the supervisor guide](docs/waitd.md), [the watcher reference](docs/wait.md), and the active [client adapter](docs/clients.md) before starting a wait.

## Invocation

- `<invoke> <condition>` starts a passive wait.
- `<invoke> resume <watcher-log>` handles a watcher event.
- `<invoke> status <watcher-log>` reads the last durable watcher result without restarting it.

Use `$wait` in Codex and the client's slash-skill form, usually `/wait`, elsewhere.

## Workflow

1. Define the read-only query, exact ready and terminal values, and finite timeout. Use the 24-hour default only when a more meaningful bound is unavailable. With a native Todo or plan tool, reuse the task's item or create one; a goal node or loop iteration keeps its caller's item.
2. Choose unique lock and log files for this object and session. Prepare a message template invoking `wait resume` with the log path, `{event_id}`, `{event}`, and `{status}`.
3. Submit through `scripts/waitctl.py start -- ...` with `--client` and the current `--session`. The command starts the local service on demand. Confirm ownership through `show WATCH_ID` and the live watcher lock; submission alone only confirms registration. Use `wait_for.py` directly for compatibility or recovery.
4. After ownership is confirmed, update the Todo item to waiting, recording the condition, deadline, and log path. End the turn while the watcher monitors the object.
5. On resume, validate the durable log and event ID, query current state once, and decide the next action. Update Todo from that verified result: complete the item when its acceptance condition is met, or record the failure, timeout, or remaining work. For goal-owned waits, the root records the DAG transition before updating Todo.

## Boundaries

- Queries must be read-only and run without an implicit shell.
- A wake-up does not authorize retrying, restarting, deploying, or otherwise mutating the external system.
- Do not start a duplicate watcher for the same object and task.
- Treat a repeated notification with the same event ID as a duplicate and do not repeat completed work.
- Report terminal, timeout, and repeated-query-failure outcomes instead of retrying indefinitely.
- Every watcher must have a finite overall timeout so a mistaken condition cannot leak a process or stall the task forever.

Run `python scripts/waitctl.py --help` and `python scripts/wait_for.py --help` for CLI details.
