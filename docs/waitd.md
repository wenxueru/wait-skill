# `waitd`: Local wait supervisor

[简体中文](waitd.zh-CN.md)

`waitd` replaces one-tmux-session-per-watcher with one local service. It keeps a durable registry, runs watcher schedules cooperatively, and exposes a Unix-socket control plane for watcher, goal, and loop commands.

```text
root agent ── waitctl ── Unix socket ── waitd
                                      ├─ watcher schedules
                                      ├─ delivery and wake handoff
                                      └─ bounded goal and loop commands
```

The root agent still decides graph structure, dispatch, recovery, acceptance, and completion. `waitd` validates and persists requested transitions but never invents nodes, dispatches agents, mutates an external system, or marks work complete on its own. Child agents report only to the root and must not invoke goal mutations.

## Start and inspect

`waitctl start`, `waitctl goal`, and `waitctl loop` start the service on demand. It can also be managed explicitly:

```bash
python scripts/waitctl.py daemon start
python scripts/waitctl.py daemon status
python scripts/waitctl.py list
python scripts/waitctl.py show WATCH_ID
python scripts/waitctl.py cancel WATCH_ID
python scripts/waitctl.py daemon stop
```

The service uses `/tmp/wait-skill-<uid>/waitd.sock` and a mode-`0600` registry inside a mode-`0700` directory. Active watchers are restored from their last durable phase with their original activation, overall, and wake-ack deadlines. A selected event is never queried again after restart; a notification interrupted with an unknown outcome is reported as `unconfirmed` instead of being replayed. The registry retains active watchers, timer records awaiting loop acknowledgement, and the 256 most recently finished other records. It also records loop state paths before state commands run; restart repairs missing timer registration.

`ping` includes a protocol version and source fingerprint. `waitctl` stops an incompatible or stale daemon, waits for its single-instance lock to be released, and then starts the current implementation. An explicit `daemon stop` uses the same handoff, so an immediate subsequent start cannot lose a race with the exiting process.

## Submit a watcher

Pass the same bounded watcher arguments after `--`:

```bash
python scripts/waitctl.py start -- \
  --label "deployment api" \
  --ready Ready \
  --terminal Failed \
  --interval 60 \
  --timeout 3600 \
  --lock-file /tmp/wait-deployment-api.lock \
  --log-file /tmp/wait-deployment-api.json \
  -- deployctl status api --output status
```

Coordination paths are resolved against the submitting working directory. Query arguments after the inner `--` are preserved exactly and executed without a shell. The service runs query commands as asynchronous subprocesses and reaps their process groups before releasing watcher ownership on shutdown.

`wait_for.py` remains available as a standalone compatibility and recovery path.

## Manage goal and loop state through the service

The root can route any existing `wait_goal.py` command through the service:

```bash
python scripts/waitctl.py goal -- init \
  --objective "Ship after CI passes" \
  --session "$AGENT_SESSION_ID"

python scripts/waitctl.py goal -- check --state "$GOAL_STATE"
python scripts/waitctl.py goal -- ready --state "$GOAL_STATE"

python scripts/waitctl.py loop -- init \
  --task "Inspect the queue" \
  --interval 600 \
  --duration 3600 \
  --session "$AGENT_SESSION_ID"

python scripts/waitctl.py loop -- show --state "$LOOP_STATE"
```

Goal and loop commands are serialized within their own command family and retain the state engines' validation, file locking, event history, and atomic writes. A command is terminated after 30 seconds so one stuck mutation cannot block its family indefinitely. The response contains the command exit `code`, parsed `output`, and any `error` text. `wait_goal.py` and `wait_loop.py` remain separate engines and compatibility entry points; durable state files, not service memory, remain the source of truth.

## Failure boundaries

- Every watcher retains its query timeout, overall timeout, failure limit, delivery limit, and finite wake acknowledgement timeout.
- Cancelling a watcher interrupts its scheduled sleep immediately. A query already running is allowed only its bounded query timeout to exit.
- If the service dies, per-watcher locks are released. `wait-goal check` then reports affected waiting nodes as `orphaned_wait`.
- Registry recovery resumes only read-only watcher commands. It does not replay goal mutations or external side effects.
- Credentials must stay out of watcher argv, message templates, registry state, logs, and goal state.
