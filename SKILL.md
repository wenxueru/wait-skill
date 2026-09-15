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

1. Identify one read-only query command, its exact ready and terminal values, and a finite overall timeout. Use the 24-hour default only when a more meaningful bound is unavailable.
2. Choose unique lock and log files for this external object and agent session. Never put credentials in command arguments, logs, or notifications.
3. Submit the watcher through `scripts/waitctl.py start -- ...` with `--client` and the current `--session`. The command starts the single local `waitd` service on demand. Use `wait_for.py` directly only as a compatibility or recovery path.
4. Set a message template that explicitly invokes `wait resume`, names the watcher log, and includes `{event_id}`, `{event}`, and `{status}`. The watcher durably records delivery progress. Codex queue failures retry at most 12 times with the stable ID; session-resume clients default to one attempt because a timeout may already have started the turn.
5. Once the watcher owns the wait, do not query the same object from model turns. End the turn.
6. On resume, read the durable log and query current state once. Treat the notification as a hint, not proof.

## Boundaries

- Queries must be read-only and run without an implicit shell.
- A wake-up does not authorize retrying, restarting, deploying, or otherwise mutating the external system.
- Do not start a duplicate watcher for the same object and task.
- Treat a repeated notification with the same event ID as a duplicate and do not repeat completed work.
- Report terminal, timeout, and repeated-query-failure outcomes instead of retrying indefinitely.
- Every watcher must have a finite overall timeout so a mistaken condition cannot leak a process or stall the task forever.

Run `python scripts/waitctl.py --help` and `python scripts/wait_for.py --help` for CLI details.
