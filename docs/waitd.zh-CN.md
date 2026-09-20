# `waitd`：本地等待服务

[English](waitd.md)

`waitd` 集中运行等待程序，通过 Unix socket 接收 `waitctl` 的 wait、goal 和 loop 命令。

```text
Agent ── waitctl ── waitd
                    ├─ 运行程序、超时、取消
                    ├─ 保存结果、投递唤醒消息
                    └─ 串行执行 goal / loop 状态命令
```

Agent 提供程序逻辑，决定如何恢复并验收结果。服务管理执行、生成唤醒消息、记录决定，不解释业务输出，也不调度子 Agent。

## 启动与查看

`waitctl start`、`waitctl goal` 和 `waitctl loop` 会按需启动服务。

```bash
python src/waitctl.py daemon start
python src/waitctl.py daemon status
python src/waitctl.py list
python src/waitctl.py show WATCH_ID
python src/waitctl.py cancel WATCH_ID
python src/waitctl.py daemon stop
```

服务使用 `/tmp/wait-skill-<uid>/waitd.sock`；目录权限为 `0700`，注册表权限为 `0600`。注册表保留活动等待、尚未收到 loop 确认的计时器，以及其他最近完成的 256 条记录。

`ping` 返回协议版本和源码指纹。服务过旧时，`waitctl` 停止旧进程并等锁释放，再启动新版；`daemon stop` 也会等待锁释放。

## 提交等待程序

按 [wait 示例](wait.zh-CN.md)准备程序：

```bash
python src/waitctl.py start -- \
  --label "deployment api" \
  --event-note "复查部署状态；成功则检查健康，失败则报告原因" \
  --client codex --session "$AGENT_SESSION_ID" \
  -- env PYTHONPATH="$PWD/src" python /tmp/wait_deploy.py
```

独立 wait 自动生成日志和锁文件路径，也可显式指定。相对路径按提交目录解析；内层 `--` 后的程序参数原样保留，不隐式调用 shell。

服务保存标准输出、标准错误和退出码，只报告 `exited`、`timeout`、`start_failed` 或 `interrupted`，不解析输出。两种输出各保留最多 64 KiB，超出部分继续读取但丢弃。取消或超时会终止程序所在的进程组。每次等待都有有限超时，默认一小时。

恢复时服务发送 `$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"`，传了 `--goal-state` 或 `--loop-state` 时使用对应变体。命令结构由服务生成，简短的 `event_note` 由提交 Agent 通过 `--event-note` 提供；两者都不取自程序输出。读日志、复查状态并决定怎么恢复仍是 Agent 的事。

## Goal 与 loop 命令

```bash
python src/waitctl.py goal -- init \
  --objective "CI 通过后发布" --session "$AGENT_SESSION_ID"
python src/waitctl.py goal -- check --state "$GOAL_STATE"
python src/waitctl.py goal -- ready --state "$GOAL_STATE"

python src/waitctl.py loop -- init \
  --task "检查队列" --interval 600 --duration 3600 \
  --event-note "读取计时日志，Ready 则开始下一轮，Expired 则结束" \
  --session "$AGENT_SESSION_ID"
python src/waitctl.py loop -- show --state "$LOOP_STATE"
```

同类命令串行执行，沿用状态引擎的校验、文件锁、事件历史和原子写入，每条命令上限 30 秒。响应包含退出 `code`、解析后的 `output` 和 `error`。持久文件仍是状态依据；也可直接使用 `wait_goal.py` 和 `wait_loop.py` 管理状态。

每轮完成后，服务注册一个阻塞计时程序；计时完成时用 `$wait-loop resume {state_file}; event_id={event_id}; log_file={log_file}; event_note="{event_note}"` 唤醒。状态路径由 loop 提供，note 来自初始化 loop 的 Agent。

## 重启与恢复

- 尚未执行的程序可以在重启后启动；已经开始但未保存结果的程序报告 `interrupted`，不擅自重跑。
- 已保存的结果继续投递，沿用原 event ID 和期限。投递中断且结果不确定时标记 `unconfirmed`，重试前先检查目标会话。
- 服务在执行状态命令前记录 loop 路径，可以补齐遗漏的计时器注册，不重复迭代。已经启动却中断的计时器同样不自动重跑。
- 服务退出后 watcher 锁释放，goal 检查会把受影响的等待节点报告为 `orphaned_wait`，由根 Agent 检查和恢复。
- 参数、输出和持久文件都不应包含凭据。

从旧状态匹配接口升级时，先检查现有等待，取消不再需要的记录，再重新提交等待程序。保留原状态查询时，编写调用 `wait_for.poll(query, evaluate)` 的脚本；旧 `wait_for.py --ready/--terminal` 命令行接口已移除。
