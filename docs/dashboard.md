# Wait dashboard

## Open

Use Python 3.11 or newer:

```sh
python /path/to/wait/src/waitctl.py dashboard --session "$AGENT_SESSION_ID"
```

- Normal Wait/Loop/Goal commands return `dashboard_url` focused on the session, task and node. Reuse the panel rather than opening a tab per command.
- `dashboard` defaults to `CODEX_THREAD_ID` when set. Otherwise pass the current session explicitly; never infer it from the newest task or browser tab.
- `dashboard --state /absolute/path/goal.json` registers an existing DAG and can infer its owner.
- An unscoped URL shows no tasks. `--all-sessions` explicitly opens the machine-wide view.
- Open the full returned URL. The port changes when waitd restarts.
- The page defaults to English and light mode. Header controls switch language and theme; this browser saves the choices for the dashboard address, including its port. A new port after restart uses the defaults.

## Tasks and controls

The session overview combines the current goal with independent waits and loops. It derives progress, waiting, pauses, failures and final verification from saved state; finished tasks fold into history.

| View | Display | Controls |
| --- | --- | --- |
| DAG | Dependencies, acceptance, results and events | Add/edit pending nodes, retry, pause/resume/cancel |
| Wait | Status, deadline, delivery, full event note and result | Cancel the waiting program |
| Loop | Task, iteration, interval, next run, deadline and history | Cancel future iterations |

Goal- and Loop-owned watchers appear with their parent instead of duplicating ordinary Wait cards. Goal health-check loops also remain accessible in Loop.

Goal pause stops new dispatch; cancel marks unfinished nodes cancelled. Neither kills running agents or external jobs. Wait cancellation stops its program; Loop cancellation stops future iterations.

## How the skill and page cooperate

- Use normal Wait/Loop/Goal commands. waitd derives the page automatically; agents do not write HTML, maintain summaries or acknowledge page changes.
- CLI and page controls use the same task state and command queues. Reread state before dispatch and respect user edits, pause and cancellation.
- SSE pushes changes without polling or starting model turns. Send a chat message when immediate agent action is needed.
- For manual continuation, ordinary Wait accepts `start -- --session ID --no-notify ...`; Loop accepts `loop -- init --session ID --no-notify ...`. This preserves session visibility and disables automatic resume turns. Block with `waitctl follow ID --timeout SECONDS`, then continue the normal workflow, including Loop `begin`/`complete`.

## Multiple sessions

- HTTP state, SSE and actions use the session in the URL. Switch explicitly between Current session and All sessions.
- One OS user shares one waitd. Session scope is a filter, not an authentication boundary.
- Separate state files keep tasks independent. Sharing a file shares control; locks prevent partial writes, not conflicting intent.
- Goal/Loop state commands are serialized by mode. Watcher programs run concurrently and retain their target session.

## Remote access

Assets are bundled and the server binds 127.0.0.1. No hosting or native Codex Goal integration is required.

```sh
ssh -L PORT:127.0.0.1:PORT HOST
```

Open the full returned URL locally, preserving the session query. Use the same port on both ends and 127.0.0.1 for Host/Origin checks. There is no multi-user authentication.
