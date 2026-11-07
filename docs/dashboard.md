# Wait dashboard

## Open your session

Use Python 3.11 or newer and the current conversation's session ID:

```sh
python /path/to/wait/src/waitctl.py dashboard --session "$AGENT_SESSION_ID"
```

- Wait/Loop/Goal commands also return a `dashboard_url` focused on their session, task and node. Reuse the panel and include its link when reporting progress, results or callback handling.
- `dashboard` uses `CODEX_THREAD_ID` when set; otherwise pass the session explicitly. An unscoped URL shows no tasks. Use `--all-sessions` only for a machine-wide view.
- To register a saved DAG, use `dashboard --state /absolute/path/goal.json`; its owner supplies the session when none was selected. Never infer ownership from the newest task or browser tab.
- Keep the full returned URL. Add `&view=graph` to open the dependency map directly.

The page defaults to English and light mode. **Settings** switches language and appearance; this browser remembers them for the current address, including its port. A new port uses the defaults.

## Read progress

The session overview combines goals with independent waits and loops. Status and progress come from saved task state; finished tasks fold into history. Goal- and Loop-owned watchers appear with their parent, while Goal health-check loops remain accessible in Loop.

| View | What you can see |
| --- | --- |
| Task list | Search titles/IDs, filter by status or click a status count; failed, running and ready tasks appear first |
| Dependency map | Dagre reduces crossings and routes long dependencies through the graph; zoom or fit the whole map |
| Task details | Full title, dependencies, acceptance criteria, recorded times, waits and results |
| Wait | Query status, deadline, delivery, full event note and result |
| Loop | Iteration, interval, next run, deadline and run history |

- Select a task or dependency link to inspect it. A current task is selected automatically; expand the objective below the heading to read its full text.
- **Updated** uses recorded node events and lifecycle times in your browser's timezone. Hover for the full timestamp. Details show available creation, start, waiting and end times; missing records show `—`.
- Search and filters keep the dependency map's structure and dim other nodes. Layout and display state are computed in the browser using bundled assets; no CDN is needed.

## Change tasks

| Control | Effect |
| --- | --- |
| Add a DAG node | Save its title, kind, dependencies, acceptance criteria and read/write scope |
| Edit a pending node | Replace its title, dependencies and acceptance criteria |
| Retry a failed node | Return it to pending; the agent still checks whether retrying external work is authorized |
| Pause/resume a Goal | Stop/allow new dispatch |
| Cancel a Goal | Mark unfinished nodes cancelled; preserve completed results |
| Cancel a Wait / Loop | Stop the waiting program / prevent future loop iterations |

Goal pause/cancellation does not terminate running agents or external jobs. Page controls update saved state; send a chat message when you need immediate agent action.

## How the agent and page cooperate

- Use normal Wait/Loop/Goal commands. waitd derives the page; agents do not write HTML, maintain page summaries or perform extra acknowledgements.
- CLI and page controls share task state and command queues. Reread before dispatch and respect user edits, pause and cancellation.
- SSE pushes changes without model polling or starting new model turns.
- HTTP state, SSE and actions use the URL's session. One OS user shares a default waitd; session scope is a filter, not an authentication boundary. Separate state files isolate tasks; shared files share control.
- Goal/Loop commands are serialized by mode; watcher programs run concurrently and deliver to their owning session.

For deliberate manual continuation, use `start -- --session ID --no-notify ...` or `loop -- init --session ID --no-notify ...`. These retain session visibility and disable automatic resume turns. Block on `waitctl follow ID --timeout SECONDS`, then continue the mode's normal event handling, including Loop `begin`/`complete`.

## App preview and remote access

The server binds `127.0.0.1`. App preview or SSH forwarding may map it to another loopback hostname and port. Keep all original query parameters, including session, mode, focus, node and view.

For a known mapped address:

```sh
python /path/to/wait/src/waitctl.py dashboard --session "$AGENT_SESSION_ID" --base-url http://localhost:63671
```

`--base-url` changes the printed URL only; it preserves the query and does not create forwarding or reconfigure waitd.

For SSH, local and remote ports may differ. This forwards local port 63671 to the remote dashboard's port 35857:

```sh
ssh -N -L 127.0.0.1:63671:127.0.0.1:35857 HOST
```

Then open `http://localhost:63671/` with the original query, or generate the scoped URL with `--base-url` above.

- Use HTTP with `localhost`, `127.0.0.1` or `[::1]`; explicit ports must be 1–65535.
- When `Origin` is present, its scheme, hostname and effective port must match the request URL.
- There is no multi-user authentication; keep forwarding listeners on loopback.

## Updating a running installation

Keep active waitd running when repairing preview access. A version mismatch leaves it untouched: use its matching installation until active watchers finish, then follow the [service lifecycle](waitd.md#service-lifecycle) to switch versions.
