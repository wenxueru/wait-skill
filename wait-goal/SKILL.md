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

1. **Plan.** Persist the objective with `init`, retain its returned `state_file`, and add the initial nodes. Each node has one bounded outcome, dependency-backed inputs, acceptance checks, expected artifacts, and a read-only or explicit write scope. Keep tightly coupled work together. With a native Todo or plan tool, display these nodes using their IDs and include final objective verification.
2. **Load and schedule.** On start or resume, read `show`, run `check`, and rebuild the task's Todo from persisted state. Execute only the frontier returned by `ready`. Reflect successful state transitions in Todo; the persisted graph and acceptance evidence determine scheduling and completion.
3. **Execute.** Start local and external nodes before acting. For an agent node, persist `prepare-agent`, include its dispatch token in the assignment, then attach the returned runtime ID with `start --dispatch-token ... --agent-id ...`. Mark dispatched work in progress. Ask children to use agent-local Todo for internal steps and return results, evidence, artifacts, and discovered work to the root. The root maintains shared Todo lists; children do not contact or wait on each other, mutate the graph, invoke goal commands, or create agents.
4. **Accept and evolve.** Check results and expected artifacts before `complete`; record failures with `fail`. Update the corresponding Todo after the state command succeeds. Add discovered work with `add --reason`, reflect it in Todo, and schedule newly ready nodes. A completed child checklist means its result is ready for root acceptance.
5. **Wait or recover.** When no node is ready, follow the current activity:
   - `dispatching`: reconcile the saved token with runtime agents before further dispatch.
   - Running agents: use the runtime's blocking wait for completion or attention.
   - External waiting: prepare `wait`, submit a bounded watcher using its returned absolute paths, confirm the startup receipt, then `activate-wait`. Mark the item's waiting condition and deadline in Todo and end the turn. On an event, read the log, record `wake`, re-check external state, and complete, fail, or establish another wait.
   - `orphaned_wait` or interrupted preparation: `abort-wait` with the current watch ID, then establish a replacement on the same node. For failed work that should continue, `retry` the original node after deciding recovery is appropriate.
   - Blocked dependencies or a required user decision: record the cause in Todo and report the decision needed.
6. **Control.** Apply pause, resume, retry, or cancellation to durable state first, then refresh affected Todo items. On recovery, use saved dispatch and watch IDs to reconcile outstanding work.
7. **Verify and finish.** When every node is complete, check the original objective. Add missing work and continue, or persist evidence with `verify` and run `finish`. Complete the final-verification Todo item after both commands succeed.

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
- After activating a watcher, set up event delivery for the session recorded at initialization using the [client adapter](../docs/clients.md), then end the turn.

Run `python ../scripts/waitctl.py --help`, `python ../scripts/wait_goal.py --help`, and `python ../scripts/wait_for.py --help` for command details.
