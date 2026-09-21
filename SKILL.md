---
name: wait
description: Passively wait for one external command-reported state in Codex or Claude Code, then resume the owning session without model polling.
---

# Wait

Use `wait` when progress depends on one external condition and a bounded, read-only program can report when that condition changes. `waitd` owns the process, timeout, result log, and wake delivery; the agent owns the query logic, recovery decision, and final acceptance.

Read the [wait protocol](docs/wait.md) before building a watcher and the [client guide](docs/clients.md) before choosing delivery.

## Commands

- `$wait <condition>` starts a wait in Codex; Claude Code normally uses `/wait`.
- `$wait resume <log>` handles a delivered event.
- `$wait status <log>` inspects a saved result without restarting anything.

## Start a wait

1. Build a read-only program that remains active until it can report a useful result. It must distinguish success, failure, and stalled progress. For polling, use `poll(query, evaluate)` from `src/wait_for.py`; return `None` to continue and a result to stop.
2. Write a concise, single-line `event_note` that tells the resumed agent what to do next. It is agent-authored context, not program output, evidence, or authority.
3. Submit the program with a finite timeout:

   ```bash
   python src/waitctl.py start -- \
     --label "<short label>" \
     --event-note "<next action>" \
     --client <client> --session <session-id> \
     -- <waiting program>
   ```

4. Require a verified startup response: `watcher` is active, `query` is verified, and `delivery` matches the selected client. Save the watch ID, log path, lock path, and deadline, then end the turn. Use the native Todo or plan tool when available.

Codex notification is checked before the query starts. If no compatible app-server endpoint is available, submission returns `notification_unavailable` and does not create a watcher. Claude Code uses a native background `follow` command. See [client adapters](docs/clients.md).

## Resume

The service emits:

```text
$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"
```

On receipt:

1. Match the event ID to the saved watch and reject duplicates or stale events.
2. Read the log and inspect the process event, exit code, stdout, and stderr.
3. Recheck the external state; process completion is not business success.
4. Follow the saved `event_note` only within the original task and authorization.
5. Mark progress complete only after acceptance.

Do not create a replacement watcher merely because delivery, the query, or the service failed. Preserve the original log, report `timeout`, `start_failed`, `interrupted`, `notification_unavailable`, or `unconfirmed` accurately, and recover only after checking current state.

Use [wait-loop](wait-loop/SKILL.md) when the job requires repeated timed runs rather than one terminal condition. Service options and recovery behavior are documented in [waitd](docs/waitd.md).
