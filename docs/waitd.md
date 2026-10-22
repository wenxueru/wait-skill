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

State files, registry, and results use unique temporary files, fsync, and atomic replacement. Transient `ENOENT` retries at most three times. The program result is written before the registry; a later watcher failure preserves that log and references it through `durable_result` when possible. Event meanings and output limits belong to the [result contract](wait.md#results).

On restart, active records receive `recovered_at` and `recovery_action`. Recovery follows the saved phase:

| Evidence | Action |
| --- | --- |
| Program never started | It may start once under the original deadline |
| `running` / `querying`, matching result log | Continue finalization/delivery with the original event ID |
| Started program without durable result | Record `interrupted`; never replay uncertain execution |
| Delivery was `attempting` | Record `unconfirmed`; inspect the original conversation before retrying |
| Delivery already accepted | Continue finalization/acknowledgement without resending |
| Loop state committed, timer registry entry missing | Restore that timer using its saved watch ID without rerunning the iteration |

Terminal watchers release locks. Duplicate event IDs and conflicting locks are rejected. Goal `check` exposes lost watcher ownership as `orphaned_wait`; recover the [original node](wait-goal.md#recovery). Recovery restores scheduling and delivery only, never authority for external mutations.

## Watcher failures

`watcher_failed` uses the ordinary [client delivery](clients.md). If persistence fails before delivery, the daemon logs the error and makes one best-effort wake with `event=watcher_failed; persistence=unavailable`, or wakes a connected Claude `follow`. This wake and its in-memory state are not durable; restart can repeat it. Durable evidence that delivery may already have succeeded prevents another send.

Wake messages display the short event ID and full agent-authored `event_note`; paths are retrieved through `show SHORT_ID`, and raw program output is never embedded. Both log and registry may be stale after a persistence failure. Inspect them before making a recovery decision.

Resolve the short ID using the [resume protocol](../SKILL.md#resume-messages).
