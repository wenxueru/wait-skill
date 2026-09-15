---
name: wait-goal
description: Implement an explicit user-invoked Goal mode as a durable, event-driven DAG across CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex. Use only when the user directly invokes wait-goal; never select it automatically from task length or complexity.
---

# Wait Goal

Run the objective as a persisted dependency graph. This is a Goal mode implementation, not a longer or upgraded form of `wait`. Activate it only when the user explicitly invokes `$wait-goal` in Codex or the client's slash-skill form, usually `/wait-goal`, elsewhere.

Use `../scripts/wait_goal.py` for durable graph state and the parent `wait` skill through `../scripts/wait_for.py` for external polling. Read [the goal protocol](../docs/wait-goal.md) before starting or resuming a goal. Read [the watcher protocol](../docs/wait.md) when a node must wait on an external command-reported state. Read [the client adapters](../docs/clients.md) before configuring session resume.

## Invocation

- `<invoke> <objective>` starts a goal.
- `<invoke> resume <state-file>` resumes from a wake-up or user request.
- `<invoke> status <state-file>` reports persisted state without advancing it.
- `<invoke> pause|cancel <state-file>` applies the requested control operation.

Here, `<invoke>` is `$wait-goal` or `/wait-goal` according to the client.

## Core behavior

1. Persist the objective before doing substantial work. Let `init` create its default per-project state under `/tmp/.wait-goal/`, retain the returned `state_file`, then persist the initial nodes there. Use `--state` only when an explicit location is needed. Add newly discovered work as nodes rather than keeping it only in conversation context.
2. Make each node as independent as practical, with one bounded outcome, explicit dependency-backed `--input` values, acceptance checks, expected artifacts, and either `--read-only` or concrete write paths. Use dependency edges for unavoidable ordering or information flow; keep tightly coupled work in one node.
3. Run `wait_goal.py check`, then run only nodes returned by `ready`. The ready frontier automatically serializes overlapping or undeclared write scopes. The root agent alone schedules work and mutates the graph. It may parallelize independent nodes, but every child reports only to the root and must not contact or wait on another child, mutate the graph, or create agents. For an agent node, persist `prepare-agent` first, include its dispatch token in the child task, then attach the returned runtime ID with `start --dispatch-token ... --agent-id ...`.
4. Mark a node complete only after its acceptance checks pass and its expected artifacts exist. Record a concise result and artifacts in state, then dispatch newly ready nodes.
5. When no node is ready:
   - If agents are running, use the runtime's blocking agent wait and resume only on a completion or attention event. If activity is `dispatching`, reconcile the saved dispatch token with runtime agents before taking any other action; never dispatch a second child blindly.
   - If only external states remain, prepare one wait per external object, start its passive watcher with the persisted client, session, and a finite overall timeout, confirm the startup receipt, activate the wait, then end the turn. Do not query the same state while its watcher owns the wait.
   - If activity is `blocked`, report the failed or cancelled dependencies and the decision needed to recover. Use `retry` only after that decision, then dispatch the reset node normally.
   - If progress requires a user decision, report the exact decision needed and stop.
6. `wait` prepares a unique watch ID while leaving the node running. Use the absolute state, log, lock, and startup paths returned by that command when starting `wait_for.py`. After the startup receipt exists, run `activate-wait`; only then may the watcher query. `wake` accepts only the active ID and returns every event to `running`. Re-check the external state once, then complete, explicitly fail, or prepare another wait. Exact duplicate events are safe no-ops. If a prepared or active watcher cannot continue, run `abort-wait` with its exact watch ID before creating a replacement.
7. Before finishing, verify the result against the original objective. If it is not satisfied, add the missing work as nodes and continue. Otherwise record the evidence with `wait_goal.py verify`, then run `wait_goal.py finish`.

## Invariants

- Waiting is event-driven. Do not spend turns on unchanged status checks or periodic progress messages.
- The graph may evolve, but it must remain acyclic and every dependency must exist.
- State mutations append atomically to the goal's event history. Supply `--reason` when adding work discovered during execution.
- Treat notifications as hints, not proof. Re-check current state before acting.
- Keep query commands read-only. A wake-up never grants permission to retry, deploy, restart, or otherwise mutate an external system.
- Do not fan out work merely to increase concurrency; subagents add token cost. Prefer one agent for a short ordered chain or overlapping writes.
- Run `abort-agent` only after confirming that the prepared dispatch did not create a live child, or after terminating that child.
- Store credentials outside argv, graph state, watcher logs, and notifications.
- Client adapters resume only the session ID recorded at goal initialization. Codex queues a message; CodeWiz, Cursor, Claude Code, and GitHub Copilot CLI resume the named session in a subprocess and default to one delivery attempt to avoid duplicate turns.

Run `python ../scripts/wait_goal.py --help` and `python ../scripts/wait_for.py --help` for command details.
