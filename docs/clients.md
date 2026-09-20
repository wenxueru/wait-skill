# Client adapters

[简体中文](clients.zh-CN.md)

Clients share the same wait and graph logic; they differ in how results return to the agent. When submitting `waitctl.py start --`, use `--client CLIENT --session ID` to select the client and conversation:

| Client | Resume command | Skill invocation |
| --- | --- | --- |
| CodeWiz | `codewiz run --session ID MESSAGE` | `/wait`, `/wait-loop`, `/wait-goal` |
| Cursor CLI | `cursor-agent --print --resume=ID MESSAGE` | `/wait`, `/wait-loop`, `/wait-goal` |
| Claude Code | Native background Bash running `waitctl.py follow WATCH_ID --timeout SECONDS` | `/wait`, `/wait-loop`, `/wait-goal` |
| GitHub Copilot CLI | `copilot --resume=ID --prompt MESSAGE` | `/wait`, `/wait-loop`, `/wait-goal` |
| Codex | `codex queue --remote ENDPOINT --thread ID --message MESSAGE` | `$wait`, `$wait-loop`, `$wait-goal` |

Pass the exact session ID for the conversation that owns the wait. `--thread` remains an alias for `--session`. `--remote` is used only by Codex.

Codex queues the message and returns promptly, so delivery failures retry at most 12 times by default with a 60-second attempt timeout. CodeWiz, Cursor, and Copilot resume a CLI session and wait for that turn to return; they default to one attempt and a one-hour timeout because a timeout is ambiguous—the turn may already be running. Override `--max-notification-attempts` only when the client can prove a failed attempt did not start the turn.

Resume commands keep the client's permission policy. Pass additional non-interactive permissions through `--resume-arg=VALUE` only within existing user authorization. Otherwise leave the task for manual resumption.

CLI resume commands run a separate process; they do not prove that an existing interactive UI received a message. Their successful result is `notification: completed`; Codex queue acceptance is `queued`. For these successful goal deliveries, the service retains the lock until root `wake`, bounded by `--wake-ack-timeout` (default 60 seconds).

On resume, the root reads the referenced log, validates the event against saved state, and continues through the [wait](wait.md), [goal](wait-goal.md), or [loop](wait-loop.md) protocol. Notification delivery does not mean the task is complete.

## Session binding

Use the owning conversation's exact `--client` and `--session`, and have the agent provide a short next step through `--event-note`. A goal-owned watcher must also use the prepared watch ID and `--goal-state`, `--goal-node`, and `--startup-file` bindings from the [goal handshake](wait.md#integrate-with-wait-goal). For a loop, use the timer's saved watch ID. The service generates the resume-command structure and preserves the agent-authored note.

## Requirements

- CLI delivery requires an installed, authenticated client on the watcher's `PATH`. Claude native delivery instead requires the owning interactive session's background Bash tool.
- The saved session must be resumable and must have access to the goal state, watcher log, and workspace.
- Do not pass credentials in the session ID, program argv, or persisted files.

## CodeWiz

Submit with `--client codewiz --session ID`, using the owning CodeWiz session ID. The service invokes `codewiz run --session ID MESSAGE` when the event is ready; no `follow` task is required. The resumed CLI turn receives the generated `/wait` resume directive as `MESSAGE`.

Ensure `codewiz` is authenticated in the service environment. If delivery times out, inspect the saved session and watcher log before retrying: the resumed turn may already have acted. This adapter does not implement a separate notification into an open UI.

## Cursor CLI

Submit with `--client cursor --session ID`, using the owning Cursor CLI session ID. The service invokes `cursor-agent --print --resume=ID MESSAGE`; the `/wait` resume directive runs in that headless turn, without a `follow` task.

For an authorized task that must apply changes in headless mode, use `--resume-arg=--force` only in an appropriately restricted environment: it bypasses interactive approval. Otherwise resume manually when permission is needed. The CLI process returning successfully does not establish delivery to an open Cursor editor conversation.

## Claude Code

Use `--client claude`. `claude --print --resume` runs a headless turn, not a notification in the open conversation. The adapter no longer calls it.

1. Submit the watcher normally, retaining its `watch_id`. For a goal, complete the startup/activation handshake first. For a loop, use the `watch_id` in the saved loop state after `complete`.
2. In the **owning root's interactive session**, use Bash with `run_in_background: true` to run `python /absolute/path/src/waitctl.py follow WATCH_ID --timeout SECONDS`. Set a finite bound covering the remaining watcher timeout. Retain the native task ID, then end the turn. Shell `&`, tmux, and a separate headless Claude process cannot substitute for the native background tool.
3. The command blocks on a service event without polling. Once a result is durable, it prints the event, log path, and resume message, then exits. Claude's native task-completion notification brings the result back to the owning conversation. Read the output and log, validate the event, then follow the wait/goal/loop resume protocol.

`notification: native_pending` means the event is available, not that Claude has handled it. The root must still run goal `wake` or loop `begin`. If the background task times out, disconnects, or is cancelled, inspect the original watch; these outcomes do not mean the external task succeeded.

Closing the Claude session ends its background task. After reopening, run `follow` on the original watch. Retained completed records return immediately, so no new watcher is needed. Without native background tasks, automatic interactive wake-up is unavailable.

This path needs no MCP configuration or Channels access.

For goal waits, the service retains ownership until root `wake`, bounded by `--notification-timeout` (default 3600 seconds from event availability). Native delivery does not use the post-delivery `--wake-ack-timeout`. If no root acknowledgement arrives, the watcher fails with `notification: unconfirmed`, preserving the external event in its log. On return, inspect that log and the goal; if the node is orphaned, recover the original node using the goal recovery protocol rather than creating a replacement. Attaching `follow` again does not extend the deadline.

## GitHub Copilot CLI

Submit with `--client copilot --session ID`, using the owning Copilot CLI session ID. The service invokes `copilot --resume=ID --prompt MESSAGE`, passing the `/wait` resume directive to the resumed CLI turn. No `follow` task is required.

The adapter supplies no automatic permission approvals. Configure only the permissions authorized for the resumed task through `--resume-arg`, or resume manually if interaction is required. Check the saved session after an ambiguous timeout before retrying. This path targets Copilot CLI, not an IDE chat panel.

## Codex

Submit with `--client codex --session ID --remote ENDPOINT`, using the owning root thread ID and its queue endpoint. The service invokes `codex queue --remote ENDPOINT --thread ID --message MESSAGE`, where `MESSAGE` is the generated `$wait resume ...` instruction. No native background `follow` task is required.

The installed `codex` command must support `queue` and be able to reach that endpoint. `notification: queued` confirms queue acceptance, not that the root has executed the message; goal `wake` and loop `begin` still happen in the resumed root. If queue delivery fails, inspect the recorded notification outcome and endpoint connectivity before recovery.

## Progress tools

During planning, use the environment's Todo or plan tool. The root updates a shared list; children use their own isolated lists or report progress to the root. Preserve unrelated items.

Update progress after saving state, verifying watcher results, and accepting completion. Match goal items by node ID and standalone waits by log path; update the same items on resume. If the tool is unavailable, continue from saved state and sync progress later.

| Execution state | Native progress item |
| --- | --- |
| Pending or blocked | Unfinished; describe unmet dependencies |
| Dispatching or running | In progress; retain dispatch/node IDs |
| Waiting | Waiting if supported; otherwise unfinished with condition and deadline |
| Accepted completion | Completed |
| Failed, cancelled, or timed out | Matching status or an explicit outcome label, distinct from success |

For large graphs, group nodes by phase and retain their IDs in the description. If only one item can be in progress, use an execution-phase item listing concurrent nodes.
