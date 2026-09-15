# `waitd`：本地等待管理服务

[English](waitd.md) | 简体中文

`waitd` 用一个本地服务替代“每个 watcher 一个 tmux session”。它持久化 watcher 注册表，以协作式调度运行多个等待，并通过 Unix socket 统一接收 watcher、goal 和 loop 命令。

```text
根 Agent ── waitctl ── Unix socket ── waitd
                                      ├─ watcher 调度
                                      ├─ 通知与 wake 交接
                                      └─ 有界执行 goal 和 loop 命令
```

根 Agent 仍然决定图结构、Agent 派发、恢复、验收和完成。`waitd` 只校验并持久化根 Agent 请求的状态转换，不会自行新增节点、派发 Agent、修改外部系统或判定任务完成。子 Agent 仍然只向根 Agent 汇报，不得调用 goal 修改命令。

## 启动与查看

`waitctl start`、`waitctl goal` 和 `waitctl loop` 会按需启动服务，也可以显式管理：

```bash
python scripts/waitctl.py daemon start
python scripts/waitctl.py daemon status
python scripts/waitctl.py list
python scripts/waitctl.py show WATCH_ID
python scripts/waitctl.py cancel WATCH_ID
python scripts/waitctl.py daemon stop
```

服务使用 `/tmp/wait-skill-<uid>/waitd.sock`，注册表权限为 `0600`，所属目录权限为 `0700`。服务重启后从最后持久化阶段恢复 active watcher，并沿用原有的 activation、总等待和 wake acknowledgement 绝对截止时间。已经确定的事件不会在重启后重新查询；通知结果不明确时会记录为 `unconfirmed`，不会盲目重放。注册表保留 active watcher、等待 loop 确认的计时器记录，以及最近 256 条其他已结束记录。执行 loop 状态命令前会记录状态路径，重启时补齐遗漏的计时器注册。

`ping` 会返回协议版本和源码指纹。`waitctl` 遇到不兼容或仍在运行旧代码的服务时，会先停止旧服务，等待单实例锁释放，再启动当前实现。显式执行 `daemon stop` 也使用同一交接流程，因此紧接着重新启动不会与退出中的旧进程争抢锁。

## 提交 watcher

在 `--` 后传入原有的有界 watcher 参数：

```bash
python scripts/waitctl.py start -- \
  --label "deployment api" \
  --ready Ready \
  --terminal Failed \
  --interval 60 \
  --timeout 3600 \
  --lock-file /tmp/wait-deployment-api.lock \
  --log-file /tmp/wait-deployment-api.json \
  -- deployctl status api --output status
```

协调文件路径按照提交命令的工作目录解析。内层 `--` 后的查询参数保持原样，并且不会隐式调用 shell。服务以异步子进程运行查询，关闭时会在释放 watcher 所有权前回收查询进程组。

`wait_for.py` 仍保留为独立兼容和故障恢复入口。

## 通过服务管理 goal 和 loop 状态

根 Agent 可以通过服务执行任意现有 `wait_goal.py` 命令：

```bash
python scripts/waitctl.py goal -- init \
  --objective "CI 通过后发布" \
  --session "$AGENT_SESSION_ID"

python scripts/waitctl.py goal -- check --state "$GOAL_STATE"
python scripts/waitctl.py goal -- ready --state "$GOAL_STATE"

python scripts/waitctl.py loop -- init \
  --task "检查队列" \
  --interval 600 \
  --duration 3600 \
  --session "$AGENT_SESSION_ID"

python scripts/waitctl.py loop -- show --state "$LOOP_STATE"
```

服务分别串行执行 goal 和 loop 命令，并保留状态引擎原有的校验、文件锁、事件历史和原子写入。单条命令超过 30 秒会被终止，避免一个卡住的修改无限阻塞同类命令。响应包含命令退出 `code`、解析后的 `output` 和 `error` 文本。`wait_goal.py` 和 `wait_loop.py` 仍是相互独立的引擎与兼容入口；持久状态文件而非服务内存仍是事实来源。

## 故障边界

- 每个 watcher 仍保留查询超时、总超时、失败上限、投递上限和有限的 wake acknowledgement 超时。
- 取消 watcher 会立即中断调度 sleep；正在执行的查询最多只运行到其 query timeout。
- 服务退出后，各 watcher lock 自动释放；`wait-goal check` 会把受影响的等待节点报告为 `orphaned_wait`。
- 注册表恢复只会重新运行只读 watcher，不会重放 goal 修改或外部副作用。
- 凭据不得写入 watcher argv、消息模板、注册表、日志或 goal 状态。
