# Wait dashboard

## Open your session

Use Python 3.11 or newer and the current conversation's session ID:

```sh
python /path/to/wait/src/waitctl.py dashboard --session "$AGENT_SESSION_ID"
```

Open the returned HTTP URL; `src/wait/dashboard/index.html` needs the service and cannot be opened as a `file://` page. Add `&view=graph` to open the map directly.

The page defaults to English and light mode. **Settings** changes language and appearance; preferences belong to this browser address, including its port.

## Session scope

| Header choice | Shows |
| --- | --- |
| **Current session** | The original session from the opened URL |
| A session ID | Only that session's Waits, Loops and Goals |
| **All sessions** | Every registered task on this waitd |

- Open the picker to load known session IDs and task counts; search by ID. Other sessions' task content stays outside the selected state and event stream.
- Switching preserves the active tab and list/map view, clears task focus, and keeps a route back to the original session.
- `dashboard` uses `CODEX_THREAD_ID` or an explicit `--session`. An unscoped URL shows no tasks; `--all-sessions` opts into the machine-wide view.
- `dashboard --state /absolute/path/goal.json` registers a saved DAG and uses its owner when no session was selected. Never infer ownership from recent tasks or tabs.

## Share updates

- Wait/Loop/Goal commands return a `dashboard_url` focused on their session, task and node. Keep its full query and reuse a working forwarded address.
- Share the link on first task presentation, substantive progress/state/result/blocker changes, or user request. Ordinary replies and unchanged callbacks need no repeated link.

## Three task views

| View | Records and details | Map |
| --- | --- | --- |
| **Waits** | Independent and parent-owned watchers; query, delivery, result and full event note | Recorded Goal/Loop ownership and known Goal dependencies |
| **Loops** | Iteration, schedule, deadline and run history | Parent Goal and one recurring Loop card |
| **Goals** | DAG nodes, acceptance criteria, results and final verification | Actual task dependencies |

- The header has only these three tabs. Counts include saved history; without `mode`, the page opens Waits.
- All views share the same list, filters, sorting and details. Context nodes in maps are inspectable but do not count as extra tasks.
- Independent waits have no inferred connections. The relationship map creates no new scheduler state.

## List and details

### Visibility and filtering

| Scope | Records |
| --- | --- |
| **Current tasks** (default) | Unfinished tasks, including failures |
| **History** | Completed and cancelled tasks |
| **All tasks** | Both; no records are deleted |

- Search by title or ID. Status checkboxes and state cards toggle multiple states (OR); **All tasks** in the status menu clears that restriction.
- Selecting historical states opens History as needed; mixing current and historical states opens All tasks. Empty results keep the filters and a history shortcut.
- Session changes preserve task scope. Changing Wait/Loop/Goal mode resets to Current tasks. Direct links reveal finished records.

### Columns and navigation

| Column | Content |
| --- | --- |
| **Dependencies / Parent task** | Recorded task links; None when no relationship exists |
| **Next step** | Original event note, or `—`; never inferred from results |
| **Status** | Task-specific meaning on hover, focus or tap |
| **Duration / Updated** | Descending → ascending → default; unknown values last |

- Links open the exact task and support new tabs. Unavailable targets stay unlinked instead of opening a different task.
- Select a task for full notes, times, evidence and related tasks. Owned watchers are controlled through their Goal or Loop.
- Default order prioritizes failed, running and ready tasks. Live updates retain filters, sort and editable drafts; live duration sorting preserves focus.

## Timing

- Duration emphasizes the current running or waiting phase; details also show total lifetime, including waits. Timers update locally each second, without network polling or agent turns.
- Goal node execution restarts its phase clock on a handled wake while retaining the original total start. Wait lifetime starts at submission; Loop lifetime starts at initialization and its phase clock follows iteration/schedule events.
- Terminal times stop counting. Missing records show Not started or `—`; timestamps use the browser's timezone and show their full value on hover.

## Map controls

- Dagre reduces crossings. Current/history maps omit unrelated finished records but retain required ancestors and current Loop timers. Within a Goal scope, status/search filters dim other nodes without moving the layout; Wait/Loop maps fit the matching relationship subset. All tasks restores the full graph.
- Zoom with the slider, +/−, Ctrl + scroll or trackpad pinch. Drag anywhere, including nodes, with mouse, touch or pen to pan; ordinary scrolling also pans. A tap/click selects a node, while dragging does not.
- Panning works even when the map fits and survives live updates. **Fit** recenters it; **100%** restores natural size. With the map focused, use `+`/`−`, `0` for 100%, or `f` to fit.
- Loop timers fold into their owner card: round, interval/next run, latest result and matching timer/failure counts.
- Loop details show five recent completed iterations and a collapsed timer history linking to original Wait records. Filters still count original records; no state is deleted.
- Map instructions and relationship explanations are under **?** (hover, focus or tap); task notes and results remain complete.
- Assets are bundled locally; no CDN or page maintenance by the agent is needed.

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
