# wait-skill

A small Codex skill for waiting on external jobs and services without spending model tokens on repeated polling.

The bundled watcher runs as an ordinary process. It executes a read-only status command at a configurable interval and sends one `codex queue` message when the status becomes ready or terminal, the wait times out, or repeated queries fail. It is backend-agnostic: PAI, Kubernetes, CI systems, and HTTP health checks can all be represented by a command that prints one state.

## Install

Copy or clone this directory into your Codex skills directory:

```bash
git clone https://github.com/YOUR_ACCOUNT/wait-skill.git ~/.codex/skills/wait-skill
```

Restart Codex so the skill is discovered.

## Direct usage

```bash
python scripts/wait_for.py \
  --label "deployment api" \
  --ready Ready \
  --terminal Failed \
  --interval 60 \
  --thread "$CODEX_THREAD_ID" \
  --lock-file /tmp/wait-deployment-api.lock \
  --log-file /tmp/wait-deployment-api.json \
  -- deployctl status api --output status
```

The query command is everything after `--`. It is executed directly, without an implicit shell. For JSON output, use `--json-path`, for example:

```bash
python scripts/wait_for.py \
  --label "CI run 42" \
  --ready completed \
  --terminal failed \
  --json-path run.status \
  --thread "$CODEX_THREAD_ID" \
  -- cicli inspect 42 --json
```

Omit `--timeout` to wait indefinitely. The default query interval is five minutes. The default repeated-query-failure limit is 12; set `--max-consecutive-failures 0` to disable it.

For a long wait, use a process-management mechanism available in the current environment. Prefer `tmux` after confirming it is installed. `codex queue` must be able to reach the existing app server identified by `--remote` (default `unix://`) and `--thread`.

## Security model

- Query commands should be read-only.
- Put credentials in environment variables or configuration files, not argv.
- Raw query stdout and stderr are never forwarded or printed.
- Notification delivery is not retried after an ambiguous failure.
- A wake-up message does not grant permission to restart or mutate the watched system. The receiving agent must verify current state and existing authority.

## Development

The script uses only the Python standard library.

```bash
python -m unittest discover -s tests -v
python /path/to/skill-creator/scripts/quick_validate.py .
```

## License

MIT
