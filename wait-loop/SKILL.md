---
name: wait-loop
description: Implement an explicit user-invoked Loop mode across CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex by scheduling bounded timer waits with the parent wait skill. Use only when the user directly invokes wait-loop; never infer it from repeated or long-running work.
---

# Wait Loop

Run a task repeatedly on a timer. This is a Loop mode implementation, not a form of `wait` selected automatically. Activate it only when the user explicitly invokes `$wait-loop` in Codex or `/wait-loop` in another supported client.

Use `../scripts/wait_loop.py` for durable loop state and the parent `wait` skill through `../scripts/wait_for.py` between iterations. Read [the loop protocol](../docs/wait-loop.md), [the watcher protocol](../docs/wait.md), and [the client adapters](../docs/clients.md) before starting.

## Invocation

- `<invoke> <interval>: <task>` starts a loop and runs the first iteration immediately.
- `<invoke> resume <state-file>` handles a timer event.
- `<invoke> status <state-file>` reports state without advancing it.
- `<invoke> cancel <state-file>` cancels future iterations.

Here, `<invoke>` is `$wait-loop` or `/wait-loop` according to the client.

## Protocol

1. Run `wait_loop.py init` with the task, interval, client, session, and a finite overall `--duration`. Retain its returned `state_file`. The 24-hour default is a safety limit, not a reason to omit a more meaningful bound. Add `--max-iterations` when the requested count is finite.
2. Execute the saved task once. The root session performs each iteration; do not create an autonomous child loop.
3. After the iteration succeeds, run `complete --summary`. If it returns `completed`, report the final outcome and stop. Otherwise retain its `watch_id` and `next_run_at`.
4. Start one `wait_for.py` watcher using the saved client and session, the loop state as `--loop-state`, the loop watch ID as `--event-id`, `wait_loop.py due` as the read-only query, and an explicit finite `--timeout`. Bind the state and watcher log in one `wait-loop resume` message, then end the model turn.
5. On a `ready` event, verify the watcher log and run `begin --event-id`. If it reports a duplicate, do not execute the task again. If delivery crossed the loop deadline, `begin` completes the loop; report completion and stop. Otherwise run the next iteration and return to step 3.
6. On `Expired`, run `expire --event-id` and stop. If the loop is cancelled, completed, or superseded while waiting, ownership validation stops the stale watcher without another model wake-up.

## Boundaries

- Use only after explicit user invocation. Do not infer Loop mode from wording such as “periodically” unless the user actually calls the skill.
- Every loop has a finite duration, and every timer watcher has its own finite timeout.
- Timer wake-ups authorize only resuming the saved loop. They do not add authority for the task's external side effects.
- Execute at most one iteration at a time. Persist completion before creating the next watcher.
- A failed or interrupted iteration must not be marked complete or silently retried. Report it and ask whether to continue or cancel.
- Do not start another watcher while the state already contains an active `watch_id`.

Run `python ../scripts/wait_loop.py --help` for state commands.
