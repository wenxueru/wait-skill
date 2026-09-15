# Client adapters

[简体中文](clients.zh-CN.md)

The graph and watcher are client-neutral. Watcher arguments submitted through `waitctl.py start --` use `--client CLIENT --session ID` to select only the final delivery step:

| Client | Resume command | Skill invocation |
| --- | --- | --- |
| CodeWiz | `codewiz run --session ID MESSAGE` | `/wait`, `/wait-loop`, `/wait-goal` |
| Cursor CLI | `cursor-agent --print --resume=ID MESSAGE` | `/wait`, `/wait-loop`, `/wait-goal` |
| Claude Code | `claude --print --resume ID MESSAGE` | `/wait`, `/wait-loop`, `/wait-goal` |
| GitHub Copilot CLI | `copilot --resume=ID --prompt MESSAGE` | `/wait`, `/wait-loop`, `/wait-goal` |
| Codex | `codex queue --remote ENDPOINT --thread ID --message MESSAGE` | `$wait`, `$wait-loop`, `$wait-goal` |

Pass the exact session ID for the conversation that owns the wait. `--thread` remains an alias for `--session`. `--remote` is used only by Codex.

Codex queues the message and returns promptly, so delivery failures retry at most 12 times by default with a 60-second attempt timeout. The other adapters resume a CLI session and wait for that turn to return; they default to one attempt and a one-hour timeout because a timeout is ambiguous—the turn may already be running. Override `--max-notification-attempts` only when the client can prove a failed attempt did not start the turn.

Resume commands keep the client's default permission policy. If a resumed turn needs permissions unavailable in non-interactive mode, pass each required option explicitly with `--resume-arg=VALUE`. For example, Cursor requires `--resume-arg=--force` to apply changes in headless mode. This bypasses interactive approval, so use it only when the user authorized the resumed task and the environment is appropriately restricted; otherwise require a manual resume.

Automatic resume targets CLI sessions. An IDE chat without a documented session-resume command can still use the durable watcher log, but it must be resumed by the user or an IDE-specific bridge.

## Progress tools

During planning, each agent uses an available native Todo or plan tool according to its runtime schema. The owning root updates a shared list; children use isolated lists when available and otherwise report progress. Preserve unrelated items.

The execution protocols specify when to update progress: after persisted state transitions, verified watcher results, and final acceptance. Identify goal items by node ID and standalone waits by their log path. Reuse those identities on resume. If the tool is unavailable or fails, proceed from durable state and refresh it on the next supported execution turn.

| Execution state | Native progress item |
| --- | --- |
| Pending or blocked | Unfinished; describe unmet dependencies |
| Dispatching or running | In progress; retain dispatch/node IDs |
| Waiting | Waiting if supported; otherwise unfinished with condition and deadline |
| Accepted completion | Completed |
| Failed, cancelled, or timed out | Matching status or an explicit outcome label, distinct from success |

For large graphs, group nodes by phase and retain their IDs in the description. If only one item can be in progress, use an execution-phase item listing concurrent nodes.

## Goal initialization

Persist the client and session with the goal:

```bash
python scripts/waitctl.py goal -- init \
  --objective "Ship after CI passes" \
  --client claude \
  --session "$AGENT_SESSION_ID"
```

Without `--state`, `init` returns a unique per-project path under `/tmp/.wait-goal/`. Use that `state_file` for later goal commands.

Start its watcher with the same values. Use a slash resume directive outside Codex:

```bash
python scripts/waitctl.py start -- \
  --client claude \
  --session "$AGENT_SESSION_ID" \
  --label "CI" \
  --ready success \
  --timeout 3600 \
  --lock-file /tmp/wait-ci.lock \
  --log-file /tmp/wait-ci.json \
  --message-template '/wait resume /tmp/wait-ci.json; event_id={event_id}; event={event}; status={status}' \
  -- ci status --output state
```

For a goal-owned watcher, also pass the prepared watch ID and the `--goal-state`, `--goal-node`, and `--startup-file` handshake arguments described in [wait.md](wait.md).

## Requirements

- The selected client CLI must be installed, authenticated, and available on the watcher's `PATH`.
- The saved session must be resumable and must have access to the goal state, watcher log, and workspace.
- Do not pass credentials in the session ID, message template, query argv, or persisted files.
