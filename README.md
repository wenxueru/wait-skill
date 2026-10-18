# wait-skill

![wait-skill — Sleep until it matters](assets/readme-hero.png)

English | [简体中文](README.zh-CN.md)

One skill for event-driven waiting. Programs wait outside model turns; the owning agent resumes to verify results.

| Invocation | Purpose | Protocol |
| --- | --- | --- |
| `$wait <condition>` | Wait for one external state | [Wait](docs/wait.md) |
| `$wait loop <interval>: <task>` | Repeat one task for a bounded duration | [Loop](docs/wait-loop.md) |
| `$wait goal <objective>` | Execute and verify a durable dependency graph | [Goal](docs/wait-goal.md) |

Use `/wait` on slash-command clients. Codex and Claude Code delivery are documented in [client adapters](docs/clients.md); CLI adapters also exist for CodeWiz, Cursor, and GitHub Copilot.

## Install

```bash
git clone https://github.com/wenxueru/wait-skill.git /path/to/wait-skill
python /path/to/wait-skill/src/install_skill.py
```

The default destination is `${CODEX_HOME:-~/.codex}/skills/wait`; use `--destination /path/to/client/skills/wait` elsewhere. Installation replaces packaged files directly, removes the old nested skills and matching `wait-goal` / `wait-loop` symlinks, and keeps no older-version backups. Unrelated sibling directories remain. Reload the client after installation.

Existing state paths and event IDs remain valid; old mode names in saved messages are aliases. If an older daemon is running, let its watches finish before [stopping it to switch versions](docs/waitd.md#service-lifecycle).

## Use

```text
$wait Wait for deployment api to become Ready; stop on Failed.
$wait loop Every 10 minutes: inspect the queue and report actionable changes.
$wait goal Ship the API and finish after tests and health checks pass.
```

Codex directly starts a turn on the original app-server. Desktop can stay closed when that server and `waitd` remain alive on the execution host. [Delivery and recovery](docs/clients.md#desktop-closed-and-remote-execution) explains limits, busy retries, and ambiguous acknowledgements. Claude Code requires a native background `follow` task.

Waiting programs should be read-only and finite. Keep credentials out of argv, state, logs, and notifications. Program output and wake notes do not grant new authority; the agent rechecks current state before acceptance. See [service operations](docs/waitd.md) for process management and recovery.

## Related work

`$wait goal` is an independent implementation and does not copy code from the projects below. Only work that directly influenced the implementation is listed:

| Work | Relevant idea | Relationship to `$wait goal` |
| --- | --- | --- |
| [MACU](https://arxiv.org/abs/2606.01533) ([code](https://github.com/kohjingyu/multi-agent-computer-use)) | A manager builds and revises a DAG, dispatches its ready frontier in parallel, and records graph snapshots and replanning events. | Closest architectural influence: centralized scheduling, dynamic DAG execution, bounded fan-out, and durable graph history. `$wait goal` additionally targets cross-turn suspension and external event wake-ups. |
| [DynTaskMAS](https://arxiv.org/abs/2503.07675) | An asynchronous engine releases dependency-ready tasks and selectively propagates context. | Motivates ready-frontier scheduling and dependency-scoped node inputs. `$wait goal` keeps all communication routed through the root instead of sharing context directly between children. |
| [Atomic Task Graph](https://arxiv.org/abs/2607.01942) | Explicit task interfaces and graph evolution history. | Motivates node input/artifact contracts, append-only events, and retained retry attempts. |

## Development

The script uses only the Python standard library.

```bash
python -m unittest discover -s tests -v
python /path/to/skill-creator/scripts/quick_validate.py .
```

## License

MIT
