# `wait-loop`：事件驱动 Loop 模式

[English](wait-loop.md) | 简体中文

根会话每次只执行一轮；两轮之间仅由父级 `wait` watcher 负责计时，因此时间未到时不消耗模型轮次。

## 生命周期

```mermaid
flowchart LR
    start((调用)) --> running[执行一轮]
    running -->|complete 且仍需继续| waiting[wait 接管计时]
    waiting -->|ready 事件 + begin| running
    running -->|达到轮数或时限| completed((完成))
    waiting -->|总时限到达| completed
    running -->|cancel| cancelled((取消))
    waiting -->|cancel| cancelled
```

第一轮立即执行。成功执行 `complete` 后，要么结束循环，要么只生成一个供下一次计时使用的 `watch_id`。下一轮执行前，`begin` 会先消费该 ID，因此重复唤醒不会重复执行任务。

## 完整示例

每十分钟检查一次队列，最多四轮，并且总时长不超过一小时：

```bash
python scripts/waitctl.py loop -- init \
  --task "检查队列并报告需要处理的变化" \
  --interval 600 \
  --duration 3600 \
  --max-iterations 4 \
  --client codex \
  --session "$AGENT_SESSION_ID"

# 设置为 init 返回的值。
LOOP_STATE=/tmp/.wait-loop/PROJECT/LOOP.json

# 完成第一轮任务后：
python scripts/waitctl.py loop -- complete \
  --state "$LOOP_STATE" \
  --summary "已检查队列，没有需要处理的变化"

# 设置为 complete 返回的值。
WATCH_ID=WATCH_ID_FROM_COMPLETE

# complete 已由服务注册下一轮计时器。
```

watcher 报告 `ready` 后，先校验日志并消费事件，再执行任务：

```bash
python scripts/waitctl.py loop -- begin \
  --state "$LOOP_STATE" \
  --event-id "$WATCH_ID"
```

如果 `begin` 返回 `duplicate: true`，不得再次执行本轮；如果返回状态为 `completed`，说明 ready 事件送达时已经超过 loop 截止时间，应直接停止，不再执行下一轮；否则执行一次已保存任务，再调用 `complete`。watcher 报告 `Expired` 时运行：

```bash
python scripts/waitctl.py loop -- expire \
  --state "$LOOP_STATE" \
  --event-id "$WATCH_ID"
```

## 状态与上限

不传 `--state` 时，`init` 会创建 `/tmp/.wait-loop/<项目名>-<路径哈希>/loop-<ID>.json` 并返回规范化路径。最近的 Git 根目录用于识别项目；macOS 返回值可能使用 `/private/tmp`。

所有 loop 状态命令都通过 `waitctl` 和本地 `waitd` 服务执行。`wait_loop.py` 仍是隔离的状态引擎和兼容入口；服务重启前后均以其持久状态文件为准。状态包含任务、客户端、会话、间隔、总截止时间、可选轮数上限、当前阶段、活动 watch ID、已完成轮次摘要和事件历史。循环有两层限制：

- `--duration` 限制整个循环，默认 24 小时。
- `--max-iterations` 可选，用于限制成功完成的轮数。

服务在 `complete` 后注册计时器，截止时间为 loop 截止时间加 60 秒。执行状态命令前先记录 loop 路径，因此重启后可以补齐遗漏的计时器，而无需重复本轮任务。计时器日志保存在 loop 状态文件旁，以 watch ID 命名。

## 恢复与取消

- `show --state FILE`：读取当前状态。
- `cancel --state FILE`：阻止后续迭代；所有权校验会在活动计时器的下一次查询或通知重试前将其停止。
- 本轮失败或中断时，保持 `running`，不要调用 `complete`；报告失败并询问重试任务还是取消循环。
- 运行 `waitctl loop -- show --state FILE` 核对计时器注册。服务会用已保存的 watch ID 补齐缺失的注册记录。已记录的投递失败需要先查看 `waitctl show WATCH_ID` 及日志，再手动恢复。
- Goal 监控在 `init` 时传入 `--goal-state FILE --goal-node ID`。服务在 goal 响应中列出关联，并在目标离开 open 状态或节点结束时取消监控。计时器所有权检查和 `begin` 也会检查该关联。

定时事件不会扩大重复任务执行外部修改的权限。每一轮都必须重新确认当前状态和已有授权。
