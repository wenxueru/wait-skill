---
name: wait-goal
description: Implement Goal mode with a durable task dependency graph across CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex. Use only when the user explicitly invokes wait-goal.
---

# Wait Goal

You are the root agent. Break down the objective, assign work, and verify results until the user's goal is actually met.

The user may be away. Do not proactively ask questions or open interactive prompts. Make small decisions within existing authorization; preserve the work and explain the blocker when essential input or permission is missing.

Manage tasks through `../src/waitctl.py goal -- ...`. Read the [goal protocol](../docs/wait-goal.md) before starting. When setting up an external wait, read the [wait protocol](../docs/wait.md) and [current client adapter](../docs/clients.md). Service operations are in [waitd](../docs/waitd.md).

## Invocation

Use `$wait-goal` in Codex and usually `/wait-goal` elsewhere:

- `<invoke> <objective>`: start.
- `<invoke> resume <state-file>`: continue.
- `<invoke> status <state-file>`: inspect without advancing.
- `<invoke> pause|cancel <state-file>`: apply the user's requested control.

## Advance the work

1. Save the objective with `init` and retain its `state_file`. Give each node clear inputs, dependencies, expected artifacts, allowed writes, and acceptance checks. Every dependency must exist, and the graph must stay acyclic.
2. On every start or resume, read `show`, run `check`, and work on the nodes returned by `ready`. Track node IDs in native Todo when available, reusing the node's item when calling wait. Update Todo after state is saved; a finished checklist is not acceptance evidence.
3. Prefer parallel execution for independent, separately verifiable tasks with non-overlapping writes. Keep short ordered chains, shared context, and overlapping edits with one agent.
4. Run `start` for local/external nodes. Before dispatching a child, run `prepare-agent`, include its dispatch token in the assignment, and attach the instance with `start --dispatch-token ... --agent-id ...`. Children may use private Todo lists but report results, evidence, artifacts, and questions only to you. They do not contact or wait on each other, edit the graph, or create agents.
5. Accept results as they arrive: `complete` verified work, `fail` failed work, and add missing work with `add --reason`. Then execute newly ready nodes. Only you edit the graph and shared Todo.

## When work must wait

Handle returned results first and look for independent work. **Keep going while tasks are executable, results await acceptance, or the objective still needs verification.**

- Waiting for children: use the runtime's blocking wait and confirm that completion or attention events reach this session. A live child alone does not establish a wake-up path.
- Waiting for external state: run `goal wait` with the program attached (`-- ... -- <waiting program>`); it prepares the node, submits the watcher, and verifies the receipt in one command — no flags to copy. Then run `activate-wait` with the returned watch ID. Record the condition, deadline, and log; set up the client wake-up path before ending the turn. The default wait is one hour. Assess task and trigger reliability before extending it; consider a linked wait-loop for hourly health and progress checks when needed.
- Genuinely blocked: preserve unfinished state and the cause, and continue other authorized work. When everything is blocked, report what is missing and where state is saved, then end the turn for later user recovery. Do not invent authorization or claim completion.

Before ending a turn with an unfinished goal, confirm a wake-up source or state a concrete blocker, pause, or cancellation. Do not leave the goal without a way to continue.

## Recover and finish

Prepare the waiting program before submitting an external wait; the resume instruction is generated automatically from the goal-state and node bindings. On its event, read output and exit code, validate the event ID, run `wake` with the logged event, and re-query current state once with a read-only command. Decide whether to complete, fail, or wait again from that fresh result, not the notification alone. Notifications grant no additional authority to retry, restart, or deploy.

Reuse the original node, dispatch token, and watch ID during recovery. Reconcile `dispatching` with existing children before acting; use `abort-agent` only after confirming none is live or terminating it. For `orphaned_wait` or interrupted preparation, run `abort-wait` with the current watch ID before rebuilding the wait. Use `retry` on the original failed node rather than creating a detached replacement. Save pause, resume, and cancellation before refreshing Todo.

Once all nodes are complete, check the user's original objective again. Add any missing work. When it is met, save evidence with `verify`, run `finish`, complete the final acceptance Todo, and deliver the result.

Keep credentials out of argv, state, logs, and notifications. Do not spend model turns polling or repeating unchanged progress while waiting.
