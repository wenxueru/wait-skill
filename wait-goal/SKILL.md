---
name: wait-goal
description: Implement an explicit user-invoked Goal mode as a durable, event-driven DAG across CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex. Use only when the user directly invokes wait-goal; never select it automatically from task length or complexity.
---

# Wait Goal

Run the objective as a persisted dependency graph.

Manage durable graph state through `../scripts/waitctl.py goal -- ...` and submit external watchers through `../scripts/waitctl.py start -- ...`. The service delegates graph validation to `wait_goal.py` and watcher semantics to `wait_for.py`. Read [the goal protocol](../docs/wait-goal.md), [the supervisor guide](../docs/waitd.md), [the watcher protocol](../docs/wait.md), and [the client adapters](../docs/clients.md) before starting.

## Invocation

- `<invoke> <objective>` starts a goal.
- `<invoke> resume <state-file>` resumes from a wake-up or user request.
- `<invoke> status <state-file>` reports persisted state without advancing it.
- `<invoke> pause|cancel <state-file>` applies the requested control operation.

Here, `<invoke>` is `$wait-goal` or `/wait-goal` according to the client.

## Core behavior

1. Persist the objective before doing substantial work. Let `init` create its default per-project state under `/tmp/.wait-goal/`, retain the returned `state_file`, then persist the initial nodes there. Use `--state` only when an explicit location is needed. Add newly discovered work as nodes rather than keeping it only in conversation context.
2. Make each node as independent as practical, with one bounded outcome, explicit dependency-backed `--input` values, acceptance checks, expected artifacts, and either `--read-only` or concrete write paths. Use dependency edges for unavoidable ordering or information flow; keep tightly coupled work in one node.
3. The root runs goal commands through `waitctl.py goal -- ...`: run `check`, then execute only nodes returned by `ready`. The service serializes commands but never decides graph changes. The ready frontier automatically serializes overlapping or undeclared write scopes. The root agent alone schedules work and requests graph mutations. It may parallelize independent nodes, but every child reports only to the root and must not contact or wait on another child, mutate the graph, invoke goal commands, or create agents. For an agent node, persist `prepare-agent` first, include its dispatch token in the child task, then attach the returned runtime ID with `start --dispatch-token ... --agent-id ...`.
4. Mark a node complete only after its acceptance checks pass and its expected artifacts exist. Record a concise result and artifacts in state, then dispatch newly ready nodes.
5. When no node is ready:
   - If agents are running, use the runtime's blocking agent wait and resume only on a completion or attention event. If activity is `dispatching`, reconcile the saved dispatch token with runtime agents before taking any other action; never dispatch a second child blindly.
   - If only external states remain, consider the long-wait option below when applicable. Otherwise prepare one wait per external object, start its passive watcher with the persisted client, session, and a finite overall timeout, confirm the startup receipt, activate the wait, then end the turn. Do not query the same state while its watcher owns the wait. If `check` reports `orphaned_wait`, recover that same node with `abort-wait` and establish a new wait.
   - If activity is `blocked`, report the failed or cancelled dependencies and the decision needed to recover. Use `retry` only after that decision, then dispatch the reset node normally.
   - If progress requires a user decision, report the exact decision needed and stop.
6. `wait` prepares a unique watch ID while leaving the node running. Submit the watcher through `waitctl.py start -- ...` with the absolute state, log, lock, and startup paths returned by that command. After the startup receipt exists, run `activate-wait`; only then may the watcher query. A `waiting` node must continuously have a watcher holding its lock; `check` reports `orphaned_wait` and `show` derives the same activity when that ownership disappears. `wake` accepts only the active ID and returns every event to `running`. Re-check the external state once, then complete, explicitly fail, or prepare another wait. Exact duplicate events are safe no-ops. If a prepared or active watcher cannot continue, run `abort-wait` with its exact watch ID before creating a replacement on the same node. If the node has failed and the same logical external work should continue, `retry` the original node; do not add a detached replacement that leaves its successors blocked.
7. Before finishing, verify the result against the original objective. If it is not satisfied, add the missing work as nodes and continue. Otherwise record the evidence with `waitctl.py goal -- verify`, then run `waitctl.py goal -- finish`.

## Invariants

- Waiting is event-driven. Do not spend turns on unchanged status checks or periodic progress messages.
- Avoid concluding that no next step is possible for more than one hour. First re-run `check` and `ready`, handle returned results and local checks, and look for independent work that can still advance the objective. When a very long external wait is genuinely necessary, prompt the root to consider `wait-loop` for a bounded read-only check about once per hour. Bind the monitor with `loop init --goal-state FILE --goal-node ID`; describe its checks and root reporting in the saved task. The service tracks this link and cancels monitors when their goal leaves open or their node ends.
- The graph may evolve, but it must remain acyclic and every dependency must exist.
- State mutations append atomically to the goal's event history. Supply `--reason` when adding work discovered during execution.
- Treat notifications as hints, not proof. Re-check current state before acting.
- Keep query commands read-only. A wake-up never grants permission to retry, deploy, restart, or otherwise mutate an external system.
- Do not fan out merely for the appearance of concurrency. Prefer parallel dispatch for nodes with clear dependencies, isolated context, independent acceptance, and non-overlapping writes; this shortens the critical path and can avoid repeated root-context work. Keep short ordered chains, tightly coupled work, and overlapping writes with one agent when their coordination cost outweighs parallelism.
- Run `abort-agent` only after confirming that the prepared dispatch did not create a live child, or after terminating that child.
- Store credentials outside argv, graph state, watcher logs, and notifications.
- Client adapters resume only the session ID recorded at goal initialization. Codex queues a message; CodeWiz, Cursor, Claude Code, and GitHub Copilot CLI resume the named session in a subprocess and default to one delivery attempt to avoid duplicate turns.

Run `python ../scripts/waitctl.py --help`, `python ../scripts/wait_goal.py --help`, and `python ../scripts/wait_for.py --help` for command details.
