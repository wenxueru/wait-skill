# 客户端适配

[English](clients.md)

依赖图和 watcher 与客户端无关。通过 `waitctl.py start --` 提交的 watcher 参数使用 `--client CLIENT --session ID` 选择最后的投递方式：

| 客户端 | 恢复命令 | Skill 调用 |
| --- | --- | --- |
| CodeWiz | `codewiz run --session ID MESSAGE` | `/wait`、`/wait-loop`、`/wait-goal` |
| Cursor CLI | `cursor-agent --print --resume=ID MESSAGE` | `/wait`、`/wait-loop`、`/wait-goal` |
| Claude Code | `claude --print --resume ID MESSAGE` | `/wait`、`/wait-loop`、`/wait-goal` |
| GitHub Copilot CLI | `copilot --resume=ID --prompt MESSAGE` | `/wait`、`/wait-loop`、`/wait-goal` |
| Codex | `codex queue --remote ENDPOINT --thread ID --message MESSAGE` | `$wait`、`$wait-loop`、`$wait-goal` |

传入持有该 wait 的准确会话 ID。`--thread` 保留为 `--session` 的兼容别名；`--remote` 只供 Codex 使用。

Codex 会把消息加入队列并快速返回，因此投递失败默认最多重试 12 次，单次超时 60 秒。其余适配器会恢复 CLI 会话并等待该轮次返回，默认只尝试一次、单次超时一小时，因为超时结果不明确——目标轮次可能已经运行。只有客户端能证明失败尝试没有启动轮次时，才覆盖 `--max-notification-attempts`。

恢复命令默认沿用客户端自身的权限策略。恢复后的轮次若需要非交互模式默认不授予的权限，应使用 `--resume-arg=值` 逐项显式传入。例如 Cursor 的 headless 模式需要 `--resume-arg=--force` 才能实际应用修改。该选项会绕过交互确认，因此只有用户已授权恢复任务且运行环境受到适当限制时才能使用；否则应要求用户手动恢复。

自动恢复面向 CLI 会话。没有公开会话恢复命令的 IDE 对话仍可使用持久化 watcher 日志，但需要用户或 IDE 专属桥接器恢复。

## 初始化目标

在目标中持久化客户端和会话：

```bash
python scripts/waitctl.py goal -- init \
  --objective "CI 通过后发布" \
  --client claude \
  --session "$AGENT_SESSION_ID"
```

不传 `--state` 时，`init` 会返回 `/tmp/.wait-goal/` 下按项目隔离的唯一路径；后续 goal 命令使用该 `state_file`。

启动 watcher 时使用相同值。Codex 以外的客户端使用斜杠恢复指令：

```bash
python scripts/waitctl.py start -- \
  --client claude \
  --session "$AGENT_SESSION_ID" \
  --label "CI" \
  --ready success \
  --timeout 3600 \
  --lock-file /tmp/wait-ci.lock \
  --log-file /tmp/wait-ci.json \
  --message-template '/wait resume /tmp/wait-ci.json; event_id={event_id}; event={event}; status={status}' \
  -- ci status --output state
```

目标所属的 watcher 还必须传入准备阶段生成的 watch ID，以及 [wait.zh-CN.md](wait.zh-CN.md) 说明的 `--goal-state`、`--goal-node` 和 `--startup-file` 握手参数。

## 要求

- 所选客户端 CLI 已安装并登录，而且位于 watcher 的 `PATH` 中。
- 保存的会话可以恢复，并能访问目标状态、watcher 日志和工作区。
- 不要在会话 ID、消息模板、查询 argv 或持久化文件中传递凭据。
