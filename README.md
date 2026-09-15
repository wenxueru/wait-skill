# wait-skill

![wait-skill — Sleep until it matters](assets/readme-hero.png)

English | [简体中文](README.zh-CN.md)

Event-driven waiting for CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex:

- `$wait` passively monitors one external state without spending model turns polling.
- `$wait-loop` runs one task repeatedly on a bounded timer.
- `$wait-goal` maintains a durable DAG with centralized agent scheduling, external waits, and final verification.

`wait` is the primary skill; the derived `wait-loop` and `wait-goal` skills reuse its watcher.

## Install

Clone the repository as `wait` under the active client's skill directory, then expose the nested derived skills beside it:

```bash
skill_dir="$HOME/.codex/skills" # use the active client's skill directory
git clone https://github.com/wenxueru/wait-skill.git "$skill_dir/wait"
ln -s "$skill_dir/wait/wait-goal" "$skill_dir/wait-goal"
ln -s "$skill_dir/wait/wait-loop" "$skill_dir/wait-loop"
```

The repository root registers `wait`; `wait-loop/` and `wait-goal/` register the derived skills. Restart or reload the client after installation. SkillHub can select the client-specific destination automatically.

## Which one to use

| Scenario | Use |
| --- | --- |
| Wait for one deployment, CI run, queue, job, or service state | `$wait` |
| A `$wait-loop` request to run one task repeatedly on a bounded schedule | `$wait-loop` |
| A `$wait-goal` request to execute and verify a goal through a durable dependency graph | `$wait-goal` |
| A running goal reaches an external state | `$wait-goal`, which delegates that wait to `$wait` |

## `$wait`: passively monitor external state

Describe the object and its ready and terminal conditions:

```text
$wait Wait for deployment api to become Ready; stop if it becomes Failed.
```

`$wait` delegates queries to one local `waitd` service. The client does not need to hold a model turn while nothing changes; the service resumes the existing session only when ready, terminal, timed out, or repeatedly unreachable. One service cooperatively manages all watcher schedules, replacing one tmux session per wait.

Submit a watcher through the service:

```bash
python scripts/waitctl.py start -- \
  --label "deployment api" \
  --ready Ready \
  --terminal Failed \
  --interval 60 \
  --timeout 3600 \
  --client codex \
  --session "$AGENT_SESSION_ID" \
  --lock-file /tmp/wait-deployment-api.lock \
  --log-file /tmp/wait-deployment-api.json \
  --message-template '$wait resume /tmp/wait-deployment-api.json; event_id={event_id}; event={event}; status={status}. Re-check external state before acting.' \
  -- deployctl status api --output status
```

Everything after `--` is executed directly without an implicit shell. JSON objects and arrays require `--json-path` to select a scalar status. Every wait has a finite overall limit: `--timeout` defaults to 24 hours. The default interval is five minutes and the consecutive-query-failure limit defaults to 12 and cannot be disabled.

See [docs/waitd.md](docs/waitd.md) for service management and [docs/wait.md](docs/wait.md) for watcher semantics and security boundaries. `wait_for.py` remains available as a standalone fallback.

## `$wait-loop`: Loop mode

```text
$wait-loop Every 10 minutes: inspect the queue and report actionable changes.
```

The first iteration runs immediately. A successful iteration schedules one bounded `$wait` timer for the next run. Durable event IDs prevent duplicate wake-ups from repeating an iteration, while total duration and optional iteration limits prevent leaked loops. Loop state commands use `waitctl.py loop -- ...`, sharing the same local service as watchers and goal commands. See [docs/wait-loop.md](docs/wait-loop.md) for the execution protocol and complete example.

## `$wait-goal`: Goal mode

```text
$wait-goal Ship the API and finish only after tests and the health check pass.
```

`$wait-goal` persists an acyclic dependency graph and append-only event history, schedules a write-safe ready frontier, keeps child agents independent and reporting to the root, delegates external nodes to the parent `$wait` skill, and verifies the original objective before finishing. The root can route DAG commands through `waitctl.py goal -- ...`; the service serializes and persists those commands but never decides graph changes. By default, each goal gets a unique state file under `/tmp/.wait-goal/<project-name>-<path-hash>/`; `--state` overrides it.

See [docs/wait-goal.md](docs/wait-goal.md) for the state model, dependency rules, resume flow, and CLI.

For an external node, first create a wait cycle:

```bash
# state_file returned by init
GOAL_STATE=/tmp/.wait-goal/PROJECT/GOAL.json

python scripts/waitctl.py goal -- wait \
  --state "$GOAL_STATE" \
  --id deploy \
  --label "deployment api" \
  --log-file /tmp/wait-deployment-api.json \
  --lock-file /tmp/wait-deployment-api.lock \
  --startup-file /tmp/wait-deployment-api.started.json
```

The command returns `watch_id` and normalized absolute coordination paths. Pass those values to the watcher with the node ID. Confirm the startup receipt and run `activate-wait`; `$wait` then monitors the external state and queues a `$wait-goal resume` event. See the linked guide for the complete command.

## Security model

- Query commands should be read-only.
- Put credentials in environment variables or configuration files, not argv.
- Raw query stdout and stderr are never forwarded or printed.
- `$wait` persists an event ID and delivery state before notification. Bounded retries reuse that ID, so duplicate wake-ups can be deduplicated safely.
- A wake-up message does not grant permission to restart or mutate the watched system. The receiving agent must verify current state and existing authority.
- The `$wait-goal` graph must remain acyclic and only the root agent may mutate it.

## Related work

`$wait-goal` is an independent implementation and does not copy code from the projects below. Only work that directly influenced the implementation is listed:

| Work | Relevant idea | Relationship to `$wait-goal` |
| --- | --- | --- |
| [MACU](https://arxiv.org/abs/2606.01533) ([code](https://github.com/kohjingyu/multi-agent-computer-use)) | A manager builds and revises a DAG, dispatches its ready frontier in parallel, and records graph snapshots and replanning events. | Closest architectural influence: centralized scheduling, dynamic DAG execution, bounded fan-out, and durable graph history. `$wait-goal` additionally targets cross-turn suspension and external event wake-ups. |
| [DynTaskMAS](https://arxiv.org/abs/2503.07675) | An asynchronous engine releases dependency-ready tasks and selectively propagates context. | Motivates ready-frontier scheduling and dependency-scoped node inputs. `$wait-goal` keeps all communication routed through the root instead of sharing context directly between children. |
| [Atomic Task Graph](https://arxiv.org/abs/2607.01942) | Explicit task interfaces and graph evolution history. | Motivates node input/artifact contracts, append-only events, and retained retry attempts. |

## Development

The script uses only the Python standard library.

```bash
python -m unittest discover -s tests -v
python /path/to/skill-creator/scripts/quick_validate.py .
```

## License

MIT
