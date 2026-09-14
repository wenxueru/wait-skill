---
name: wait
description: Passively wait for one external command-reported state without spending model turns polling. Use for CI, deployment, queue, job, or service readiness. Use wait-goal for dependency graphs or coordinated multi-step work.
---

# Wait

Move repeated checks for one external object into `../scripts/wait_for.py`, then end the model turn until its event wakes the current task. Read [the watcher reference](../docs/wait.md) before starting a wait.

## Invocation

- `$wait <condition>` starts a passive wait.
- `$wait resume <watcher-log>` handles a watcher event.
- `$wait status <watcher-log>` reads the last durable watcher result without restarting it.

## Workflow

1. Identify one read-only query command, its exact ready values, terminal values, and any overall timeout requested by the user.
2. Choose unique lock and log files for this external object and Codex task. Never put credentials in command arguments, logs, or notifications.
3. Start `../scripts/wait_for.py` with the current thread ID. For a long wait, use a process manager available in the environment; prefer `tmux` only after confirming it exists.
4. Set a message template that explicitly invokes `$wait resume`, names the watcher log, and includes `{event_id}`, `{event}`, and `{status}`. The watcher durably records delivery progress. Definite failures and ambiguous client timeouts retry with the same stable ID, allowing the receiver to deduplicate repeated wake-ups safely.
5. Once the watcher owns the wait, do not query the same object from model turns. End the turn.
6. On resume, read the durable log and query current state once. Treat the notification as a hint, not proof.

## Boundaries

- Queries must be read-only and run without an implicit shell.
- A wake-up does not authorize retrying, restarting, deploying, or otherwise mutating the external system.
- Do not start a duplicate watcher for the same object and task.
- Treat a repeated notification with the same event ID as a duplicate and do not repeat completed work.
- Report terminal, timeout, and repeated-query-failure outcomes instead of retrying indefinitely.
- Use `$wait-goal` when multiple steps, agents, dependencies, or objective-level verification are required.

Run `python ../scripts/wait_for.py --help` for CLI details.
