# `waitd` service

[简体中文](waitd.zh-CN.md)

`waitd` is the local control plane for wait, goal, and loop operations. Agents define read-only waiting programs and decide how to handle results. The service owns process execution, deadlines, locks, durable logs, and wake delivery.

```text
agent -> waitctl -> waitd -> waiting program
                         -> result log
                         -> client notification
```

## Service lifecycle

`waitctl start`, `waitctl goal`, and `waitctl loop` start the service when needed. Manual controls are:

```bash
python src/waitctl.py daemon start
python src/waitctl.py daemon status
python src/waitctl.py list
python src/waitctl.py show WATCH_ID
python src/waitctl.py cancel WATCH_ID
python src/waitctl.py daemon stop
```

The Unix control socket is `/tmp/wait-skill-<uid>/waitd.sock`. Its directory uses mode `0700`; the registry uses `0600`. A protocol version and source fingerprint prevent an old daemon from silently serving new code. The registry retains active watches, loop timers awaiting acknowledgement, and the latest 256 other completed records.

## Starting a watcher

`waitctl start` has two argument boundaries:

```text
waitctl.py start -- <waitd options> -- <waiting program and its arguments>
```

The first `--` ends `waitctl` parsing. The second separates watcher options from the program. Legacy status-matching flags such as `--ready` and `--terminal` are not watcher options; put that logic inside the waiting program, normally with `wait_for.poll`.

Complete Codex example:

```bash
python src/waitctl.py start -- \
  --label "deployment api" \
  --event-note "Recheck deployment; verify health on success, report the cause on failure" \
  --client codex \
  --session "$CODEX_THREAD_ID" \
  --remote "unix:///path/to/app-server-control.sock" \
  --timeout 3600 \
  -- env PYTHONPATH="$PWD/src" python /tmp/wait_deploy.py
```

Before returning success, `waitctl` verifies that:

- the daemon accepted the watcher and `show` can retrieve it;
- the watcher holds its lock while active;
- the waiting program was spawned successfully;
- the notification channel has an explicit state;
- the watcher has a finite deadline.

The successful response exposes these separately:

```json
{
  "watcher": "active",
  "query": "verified",
  "delivery": "ready",
  "session": "<thread-id>",
  "deadline": 1700000000.0
}
```

For Codex, `ready` means a Unix app-server endpoint completed a WebSocket upgrade; explicit web endpoints are `configured`. For Claude Code, `native_required` means the root still needs to attach a native background `follow` task. A watcher without a session is `disabled` for automatic delivery.

If startup cannot be verified within five seconds, `waitctl` cancels the watcher and exits nonzero. A program that cannot be spawned also exits nonzero. Its diagnostic log and terminal registry record remain available; the lock is released. This preserves failure evidence without leaving an active half-started watcher.

## Program and result contract

The waiting program runs directly, without an implicit shell. Relative coordination paths are resolved from the submitter's directory; program arguments after the second `--` are unchanged. Standalone waits receive generated log and lock paths unless explicit paths are supplied.

The service records one process event:

- `exited` with the program exit code;
- `timeout` when the watcher deadline expires;
- `start_failed` when the executable cannot start;
- `interrupted` when a service restart makes replay unsafe.

Stdout and stderr are each capped at 64 KiB. Cancellation and timeout terminate the program's process group. The service does not interpret business status, so agents must read the log and recheck external state.

The generated wake message is:

```text
$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"
```

Goal and loop variants add their state identifiers. `event_note` comes from the submitting agent, never from program output.

## Notification states

Codex notification is preflighted before the program starts. A missing or incompatible Unix endpoint returns `notification_unavailable` with `query_status: not_started`; no watcher is created. At delivery, `codex queue` stdout and stderr use the same 64 KiB bounds. Connection and protocol failures become `notification_unavailable` without retrying; other rejections use the configured bounded retry policy.

Claude Code delivery becomes `native_pending` when the result is durable. The owning conversation receives it through `waitctl follow`, as described in [client delivery](clients.md).

## Goal and loop operations

State commands run through the same service:

```bash
python src/waitctl.py goal -- init --objective "Release after CI" --session "$SESSION_ID"
python src/waitctl.py goal -- check --state "$GOAL_STATE"
python src/waitctl.py loop -- init \
  --task "Check the queue" --interval 600 --duration 3600 \
  --event-note "Read the timer event and begin the next check" \
  --session "$SESSION_ID"
```

Commands of the same state type are serialized and limited to 30 seconds. Durable state files remain authoritative. Loop timer registration is recorded before execution so restart reconciliation can restore a missing registration without repeating an iteration.

## Restart and recovery

- A registered program that never started may start after daemon recovery. A program that might already have acted is not replayed; it becomes `interrupted`.
- A durable result resumes notification with the same event ID and deadline. An interrupted, ambiguous delivery becomes `unconfirmed`.
- Watcher locks are released on terminal state. Goal checks report affected nodes as `orphaned_wait` for root recovery.
- Duplicate event IDs and duplicate lock ownership are rejected.
- Recovery never grants authority to retry external mutations.

When migrating from the removed status-matching CLI, first inspect and cancel obsolete watches. Replace `--ready` and `--terminal` with a waiting script that calls `wait_for.poll(query, evaluate)`.
