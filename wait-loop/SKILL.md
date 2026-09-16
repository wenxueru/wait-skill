---
name: wait-loop
description: Implement Loop mode across CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex by scheduling bounded timer waits with the parent wait skill. Use when the user directly invokes wait-loop or the root deliberately selects periodic health checks for a long wait in wait or wait-goal.
---

# Wait Loop

Run a task repeatedly on a timer without assuming the user is present. Do not proactively ask questions or invoke interactive question tools.

Manage the loop through `../src/waitctl.py loop -- ...`. The service uses `wait` to schedule the next run. Before starting, read the [loop protocol](../docs/wait-loop.md) and [current client adapter](../docs/clients.md). Timer details are in [wait](../docs/wait.md), and service commands in [waitd](../docs/waitd.md).

## Invocation

- `<invoke> <interval>: <task>` starts a loop and runs the first iteration immediately.
- `<invoke> resume <state-file>` handles a timer event.
- `<invoke> status <state-file>` reports state without advancing it.
- `<invoke> cancel <state-file>` cancels future iterations.

Here, `<invoke>` is `$wait-loop` or `/wait-loop` according to the client.

## Protocol

1. Run `waitctl.py loop -- init` with the task, interval, client, session, and a finite overall `--duration`. Retain its returned `state_file`. Each timer resumes automatically with `$wait-loop resume {state_file}; event_id={event_id}; log_file={log_file}` — nothing to prepare here. The default is 24 hours; choose a shorter duration when it fits the task. Add `--max-iterations` when the requested count is finite.
2. Execute the saved task once, using reasonable defaults for reversible choices within the task and existing authorization. The root session performs each iteration; do not create an autonomous child loop. Reuse the iteration's Todo item when calling wait.
3. Resolve the iteration:
   - Success: run `complete --summary`. If it returns `completed`, report the outcome and stop; otherwise retain `watch_id` and `next_run_at`.
   - Failure, interruption, or missing essential input or authorization: leave the iteration in `running`, report the blocker and state-file path, and end the turn. Do not call `complete` or silently retry. Resume from saved state when the user supplies direction.
4. Before `complete` returns, the service registers the next timer. Use its watch ID to set up the [client wake-up path](../docs/clients.md), then end the turn. The service repairs missing timer registration after restart; it reports interrupted execution instead of replaying it. Inspect timer failures using the [recovery protocol](../docs/wait-loop.md#recovery-and-cancellation).
5. Verify the timer log: `event: exited`, `exit_code: 0`, and stdout `Ready` allow `begin --event-id`. If it reports a duplicate, do not execute the task again. If delivery crossed the loop deadline, `begin` completes the loop; report completion and stop. Otherwise run the next iteration and return to step 3.
6. For timer stdout `Expired`, run `expire --event-id` and stop. If the loop is cancelled, completed, or superseded while waiting, ownership validation stops the stale watcher without another model wake-up.

## Boundaries

- Every loop has a finite duration, and every timer watcher has its own finite timeout.
- Timer wake-ups authorize only resuming the saved loop. They do not add authority for the task's external side effects.
- Execute at most one iteration at a time. Persist completion before creating the next watcher.
- Do not start another watcher while the state already contains an active `watch_id`.

Run `python ../src/waitctl.py --help` and `python ../src/waitctl.py loop -- --help` for commands.
