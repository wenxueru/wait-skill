# wait-skill

![wait-skill — Sleep until it matters](assets/readme-hero.png)

English | [简体中文](README.zh-CN.md)

Event-driven waiting for CodeWiz, Cursor, Claude Code, GitHub Copilot, and Codex:

- `$wait` passively monitors one external state without spending model turns polling.
- `$wait-loop` runs one task repeatedly on a bounded timer.
- `$wait-goal` breaks an objective into dependent tasks; the root assigns work and verifies the final result.

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

Describe what to wait for and when to stop:

```text
$wait Wait for deployment api to become Ready; stop if it becomes Failed.
```

The agent prepares a waiting program. One local `waitd` service runs the program, saves its output and exit code, and resumes the session on exit, timeout, or interruption through the [client adapter](docs/clients.md), using a fixed instruction it generates itself. The agent decides what the result means.

After preparing `/tmp/wait_deploy.py` as in the [running example](docs/wait.md), submit it. Log and lock paths are generated automatically:

```bash
python src/waitctl.py start -- \
  --label "deployment api" \
  --event-note "Recheck deployment; health-check success or report failure" \
  --client codex \
  --session "$AGENT_SESSION_ID" \
  -- env PYTHONPATH="$PWD/src" python /tmp/wait_deploy.py
```

The program after the inner `--` runs without an implicit shell. It owns polling, output parsing, and stopping conditions; the service imposes a finite `--timeout`, defaulting to one hour. For longer waits, assess task stability and trigger reliability; consider `wait-loop` for periodic health and progress checks.

See [docs/waitd.md](docs/waitd.md) for service management and [docs/wait.md](docs/wait.md) for watcher semantics and security boundaries. `wait_for.py` provides `poll(query, evaluate)` for custom waiting scripts.

## `$wait-loop`: Loop mode

```text
$wait-loop Every 10 minutes: inspect the queue and report actionable changes.
```

The first iteration runs immediately; each successful iteration schedules the next. Saved event IDs prevent duplicate runs. Set a total duration and, if needed, an iteration limit. Manage the loop with `waitctl.py loop -- ...`; see the [protocol and example](docs/wait-loop.md).

## `$wait-goal`: Goal mode

```text
$wait-goal Ship the API and finish only after tests and the health check pass.
```

The root saves a task dependency graph, dispatches independent work in parallel, and checks each result. Children report only to the root. External waits use `$wait`; before ending a turn, the root confirms a wake-up path or reports a genuine blocker. Once the tasks are done, it checks the original objective before finishing.

Manage the graph with `waitctl.py goal -- ...`. The service saves and validates changes; the root makes scheduling decisions. State is saved under `/tmp/.wait-goal/<project-name>-<path-hash>/` unless `--state` specifies another location.

See [docs/wait-goal.md](docs/wait-goal.md) for the state model, dependency rules, resume flow, and CLI.

## Security model

- Waiting programs should be read-only.
- Put credentials in environment variables or configuration files, not argv.
- Program stdout and stderr are saved, up to 64 KiB each. Keep secrets out of output; the resume instruction references the log rather than automatically embedding it.
- `$wait` persists an event ID and delivery state before notification. Bounded retries reuse that ID, so duplicate wake-ups can be deduplicated safely.
- The submitting agent writes a short `event_note` describing the next step. It must not contain credentials or copied external output, and it does not authorize restarting or mutating the watched system.
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
