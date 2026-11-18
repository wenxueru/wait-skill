# `waitd`: service operations

[简体中文](waitd.zh-CN.md)

`waitctl` talks to one local `waitd`, which owns watcher processes, deadlines, locks, logs, and delivery. Goal and Loop state commands use the same service; commands of each state type are serialized and bounded to 30 seconds. Agents own query logic and acceptance.

## Service lifecycle

`start`, `goal`, and `loop` start the service on demand. Manual controls:

```bash
python /path/to/wait/src/waitctl.py daemon start
python /path/to/wait/src/waitctl.py daemon status
python /path/to/wait/src/waitctl.py list
python /path/to/wait/src/waitctl.py show WATCH_ID
python /path/to/wait/src/waitctl.py cancel WATCH_ID
python /path/to/wait/src/waitctl.py daemon stop
```

The Unix socket is `/tmp/wait-skill-<uid>/waitd.sock`; its directory uses mode `0700`, and the registry uses `0600`. Protocol/source-fingerprint mismatch returns an error without stopping the existing daemon. To switch versions, explicitly stop it after active watches finish. The registry keeps active watches, Loop timers awaiting acknowledgement, and the latest 256 other terminal records.

## Watcher submission

```text
waitctl.py start -- <watcher options> -- <waiting program and arguments>
```

The client verifies the record, held lock while active, spawned program, delivery state, and finite deadline before returning success. It cancels startup that cannot be verified within five seconds. A fast program may finish before the response. Program spawn failure leaves a terminal record and diagnostic log, and releases the lock. Use the [Wait workflow](wait.md#workflow) for a complete submission example.

| Response field | Meaning |
| --- | --- |
| `watch_id`, `log_file`, `lock_file` | Durable identity and evidence; generated for standalone waits unless overridden |
| `watcher` | Current watcher lifecycle state |
| `query: verified` | Program startup was observed |
| `delivery` | Client-specific readiness, not business acceptance; see [clients](clients.md) |
| `session`, `deadline` | Owning conversation and finite program deadline |

Coordination paths resolve from the submitter's directory. Program argv after the inner `--` stays unchanged and runs without an implicit shell. Timeout/cancellation terminate its whole process group. Legacy `--ready` / `--terminal` status matching belongs in a [waiting program](wait.md#waiting-program-contract), not watcher options.

## Persistence and recovery

Wait writes the result log before the registry. A watcher failure can reference the existing log through `durable_result`. See the [result contract](wait.md#results) for event fields and output limits.

After a restart, active records include `recovered_at` and `recovery_action`. Wait resumes from the saved phase.

| Saved state | Recovery |
| --- | --- |
| Program never started | Start it within the original deadline. |
| Program was running and its result log matches | Use the saved result to finish the watch or deliver its original event. |
| Program started and its result is missing | Record `interrupted` for review. |
| Event pending, busy, or offline | Continue delivery from the saved result and retry time. |
| Delivery was `attempting` | Record `unconfirmed`. Check the original conversation before another wake. |
| Delivery already accepted | Finish the watch or wait for the Goal acknowledgement. |
| Loop saved its next run but the timer is missing | Restore the timer with its saved watch ID. |

Finished watches release their locks. If Goal `check` reports `orphaned_wait`, recover the [original node](goal.md#recovery).

## Watcher failures

A saved `watcher_failed` result follows the [client delivery rules](clients.md). If saving an attempt fails, Wait records the storage error in the daemon log and holds the result in memory. A connected Claude Code `follow` can still read it. External wake delivery requires a saved attempt.

Use `show SHORT_ID` and the original conversation to inspect the result and any delivery evidence. Then follow the [resume steps](../SKILL.md#resume-messages).
