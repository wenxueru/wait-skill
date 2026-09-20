# `waitd`: Local wait supervisor

[简体中文](waitd.zh-CN.md)

`waitd` runs waiting programs in one local service and accepts wait, goal, and loop commands from `waitctl` over a Unix socket.

```text
Agent ── waitctl ── waitd
                    ├─ program execution, timeout, cancellation
                    ├─ result persistence and resume delivery
                    └─ serialized goal / loop state commands
```

The agent supplies program logic, decides how to recover, and accepts results. The service manages execution, generates the resume instruction, and records decisions; it does not interpret business output or schedule child agents.

## Start and inspect

`waitctl start`, `waitctl goal`, and `waitctl loop` start the service on demand.

```bash
python src/waitctl.py daemon start
python src/waitctl.py daemon status
python src/waitctl.py list
python src/waitctl.py show WATCH_ID
python src/waitctl.py cancel WATCH_ID
python src/waitctl.py daemon stop
```

The service uses `/tmp/wait-skill-<uid>/waitd.sock`, with a mode-`0600` registry inside a mode-`0700` directory. It retains active watches, timers awaiting loop acknowledgement, and the 256 most recently finished other records.

`ping` reports the protocol version and source fingerprint. When the service is outdated, `waitctl` stops it and waits for its lock before starting the new version. `daemon stop` also waits for lock release.

## Submit a waiting program

Prepare the program as in the [wait example](wait.md):

```bash
python src/waitctl.py start -- \
  --label "deployment api" \
  --event-note "Recheck deployment; health-check success or report failure" \
  --client codex --session "$AGENT_SESSION_ID" \
  -- env PYTHONPATH="$PWD/src" python /tmp/wait_deploy.py
```

Standalone log and lock paths are automatic; explicit paths override them. Paths are resolved against the submitting directory. Program arguments after the inner `--` are preserved and executed without a shell.

The service captures stdout, stderr, and the exit code. It reports `exited`, `timeout`, `start_failed`, or `interrupted`, without parsing output. Each stream is limited to 64 KiB; excess output is drained and discarded. Cancellation and timeout stop the program's process group. Every wait has a finite timeout, defaulting to one hour.

On resume, the service sends `$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"`, or the goal/loop variant. The service builds the command structure; the submitting agent supplies the short note through `--event-note`. Neither comes from program output. The agent still reads the log, rechecks state, and decides how to recover.

## Goal and loop commands

```bash
python src/waitctl.py goal -- init \
  --objective "Ship after CI passes" --session "$AGENT_SESSION_ID"
python src/waitctl.py goal -- check --state "$GOAL_STATE"
python src/waitctl.py goal -- ready --state "$GOAL_STATE"

python src/waitctl.py loop -- init \
  --task "Inspect the queue" --interval 600 --duration 3600 \
  --event-note "Read timer log; begin on Ready or stop on Expired" \
  --session "$AGENT_SESSION_ID"
python src/waitctl.py loop -- show --state "$LOOP_STATE"
```

Each command family is serialized and uses its state engine's validation, locking, event history, and atomic writes. Commands have a 30-second limit. Responses contain exit `code`, parsed `output`, and `error` text. Durable files remain the source of truth; `wait_goal.py` and `wait_loop.py` can also manage state directly.

After each completed iteration, the service registers a blocking timer program and resumes with `$wait-loop resume {state_file}; event_id={event_id}; log_file={log_file}; event_note="{event_note}"`. The loop supplies its state path and preserves the note authored at initialization.

## Restart and recovery

- A queued program can start after restart. If execution started but no result was saved, report `interrupted` instead of replaying it.
- Saved results continue delivery with the same event ID and deadlines. An interrupted delivery with an unknown outcome becomes `unconfirmed`; inspect the destination before retrying.
- The registry records loop paths before state commands run, allowing missing timer registration to be repaired without repeating an iteration. A timer whose execution was interrupted follows the same no-replay rule.
- If the service dies, watcher locks are released. Goal checks report affected waiting nodes as `orphaned_wait`; the root must inspect and recover them.
- Keep credentials out of argv, output, and persisted files.

When upgrading from status-matching submissions, inspect existing waits, cancel obsolete ones, and resubmit a waiting program. For status queries, write a script using `wait_for.poll(query, evaluate)`; the old `wait_for.py --ready/--terminal` CLI is removed.
