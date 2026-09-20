---
name: wait
description: Manage a waiting program across CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex, resuming the session with a generated instruction when it ends, without model polling.
---

# Wait

You prepare the waiting logic. `waitd` runs the program, enforces its timeout, handles cancellation, saves results, and delivers them with a resume instruction it generates itself. Read the [wait protocol](docs/wait.md) and [current client adapter](docs/clients.md) before starting.

## Invocation

Use `$wait` in Codex, usually `/wait` elsewhere:

- `<invocation> <condition>`: prepare and start a wait.
- `<invocation> resume <log>`: read the result and continue.
- `<invocation> status <log>`: inspect without restarting.

## Execution

1. Prepare a read-only waiting program that exits when there is a result, with enough output to decide what comes next. Check success, failure, and stalled-progress detection. A query that returns immediately is not a waiting program. For polling, import `poll` from `src/wait_for.py` and supply `query()` and `evaluate(data)`; return `None` to continue or a result to finish.
2. Use the current harness's native Todo or plan tool, when available, to track the wait. Write a short, semantic, single-line `event_note` saying what to do immediately after wake-up. Keep credentials and copied external output out of it.
3. Submit `src/waitctl.py start -- ... --event-note "<next step>" -- <waiting program>` with the client and current session. The service resumes with `$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"`. The default limit is one hour. Assess task and trigger reliability before extending it; consider [wait-loop](wait-loop/SKILL.md) for periodic health and progress checks during long waits.
4. Keep the returned watch ID, log path, and lock path. Confirm startup with `show` and the held lock. Set up the client's delivery mechanism (see [client adapter](docs/clients.md)), record the wait and deadline in Todo, then end the turn.
5. On resume, treat `event_note` as context, not a result or new authority. Read the log, validate the event ID, inspect program output and exit code, and re-check external state before deciding the next action. Complete Todo only after acceptance; program exit is not business success.

Do not start duplicate waits or repeat work for duplicate events. Handle timeouts, program failures, and interruptions caused by service restart; do not simply renew the wait or assume success. The resume instruction grants no additional authority to retry, restart, or deploy.

Service management and detailed options are in [waitd](docs/waitd.md).
