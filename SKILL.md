---
name: wait-skill
description: Passively wait for an external job or service state and wake an existing Codex thread once a ready, terminal, timeout, or repeated-query-failure condition is reached. Use for long queue, deployment, CI, batch, or service waits that should not spend model tokens polling.
---

# Passive Wait

Use `scripts/wait_for.py` to move polling into a small local process. The query command is platform-specific; the wait and Codex notification logic is not.

## Workflow

1. Choose a read-only query command that prints exactly one status, or JSON plus `--json-path`. Keep credentials in the command's environment or config files, never in argv or its output.
2. Define exact ready and terminal statuses. A terminal status wakes the thread but does not authorize a retry, rebuild, or other mutation.
3. Start one watcher with a lock file and a durable log using an available process-management mechanism. Prefer `tmux` after confirming it is installed; otherwise use a mechanism supported by the current environment or ask the user how the watcher should be kept running.
4. Tell the user what is being watched and what event will wake the thread. Do not actively poll the same state while the watcher is responsible for it.
5. When the queued message arrives, re-read the external state before acting. Treat it as a notification, not proof that a resource remains ready.

Example:

```bash
python /path/to/wait-skill/scripts/wait_for.py \
  --label 'build 42' \
  --ready Running --terminal Failed --terminal Succeeded \
  --thread "$CODEX_THREAD_ID" \
  --lock-file /tmp/wait-build-42.lock \
  --log-file /tmp/wait-build-42.json \
  -- buildctl status 42 --output status
```

If the query emits `{"job": {"status": "Running"}}`, add `--json-path job.status`.

## Safety invariants

- The watcher executes the query directly without an implicit shell. Use an explicit `bash -lc` only when shell syntax is genuinely required.
- It never prints raw query output; only a validated short status enters its result and notification.
- Notification delivery is attempted once. An ambiguous delivery failure is not retried because the first message may already be queued.
- Use a unique lock file per external object and Codex thread to prevent duplicate watchers.
- Do not use a mutating query command. The watcher does not expand the user's authorization or the scope of the task.

Run `python scripts/wait_for.py --help` for all options. The script has no third-party Python dependencies.
