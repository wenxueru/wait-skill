# wait-skill

English | [简体中文](README.zh-CN.md)

Two event-driven Codex skills:

- `$wait` passively monitors one external state without spending model turns polling.
- `$wait-goal` runs a durable multi-step objective with dependencies, independent agents, external waits, and final verification.

Both reduce active polling and token use. `$wait` works independently and also powers external nodes inside `$wait-goal`.

## Install

Copy or clone this directory into your Codex skills directory:

```bash
git clone https://github.com/wenxueru/wait-skill.git ~/.codex/skills/wait-goal
ln -s ~/.codex/skills/wait-goal/wait ~/.codex/skills/wait
```

The repository root registers `$wait-goal`; the `wait/` subdirectory registers `$wait`. Restart Codex after creating the symlink so both skills are discovered.

## Which one to use

| Scenario | Use |
| --- | --- |
| Wait for one deployment, CI run, queue, job, or service state | `$wait` |
| Run work with multiple steps or dependencies | `$wait-goal` |
| Coordinate independent agents | `$wait-goal` |
| Verify the original request after every step completes | `$wait-goal` |
| Pause a goal for an external system | `$wait-goal` using `$wait` |

## `$wait`: passively monitor external state

Describe the object and its ready and terminal conditions:

```text
$wait Wait for deployment api to become Ready; stop if it becomes Failed.
```

`$wait` delegates queries to a normal local Python process. Codex does not need to hold a model turn while nothing changes; the watcher wakes the existing task only when ready, terminal, timed out, or repeatedly unreachable.

The watcher can also be run directly:

```bash
python scripts/wait_for.py \
  --label "deployment api" \
  --ready Ready \
  --terminal Failed \
  --interval 60 \
  --thread "$CODEX_THREAD_ID" \
  --lock-file /tmp/wait-deployment-api.lock \
  --log-file /tmp/wait-deployment-api.json \
  --message-template '$wait resume /tmp/wait-deployment-api.json; event_id={event_id}; event={event}; status={status}. Re-check external state before acting.' \
  -- deployctl status api --output status
```

Everything after `--` is executed directly without an implicit shell. Use `--json-path` to select a scalar status from JSON output. Omit `--timeout` to wait indefinitely. The default interval is five minutes and the default consecutive-query-failure limit is 12.

See [docs/wait.md](docs/wait.md) for the complete workflow, CLI, and security boundaries.

## `$wait-goal`: run a durable objective

Start a multi-step objective without native `/goal`:

```text
$wait-goal Ship the API and finish only after tests and the health check pass.
```

`$wait-goal` persists an acyclic dependency graph and append-only event history under `.wait-goal/`, schedules a write-safe ready frontier from explicit read-only or write-scope contracts, keeps child agents independent and reporting to the root, stops model turns when no work is actionable, delegates external nodes to `$wait`, and verifies the original objective before finishing.

See [docs/wait-goal.md](docs/wait-goal.md) for the state model, dependency rules, resume flow, and CLI.

For an external node, first create a wait cycle:

```bash
python scripts/wait_goal.py wait \
  --state .wait-goal/release.json \
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
- `$wait` persists an event ID and delivery state before notification. Definite failures and ambiguous timeouts retry with the same ID, so duplicate wake-ups can be deduplicated safely.
- A wake-up message does not grant permission to restart or mutate the watched system. The receiving agent must verify current state and existing authority.
- The `$wait-goal` graph must remain acyclic and only the root agent may mutate it.

## Related work

`$wait-goal` is an independent Codex-oriented implementation; it does not copy code from the projects below. The following works directly influenced its design:

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
