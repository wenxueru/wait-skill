# `waitd` 本地服务

[English](waitd.md)

`waitd` 是 wait、goal 和 loop 的本地控制面。Agent 定义只读等待程序并决定如何处理结果；服务负责进程、截止时间、锁、持久日志和唤醒投递。

```text
Agent -> waitctl -> waitd -> 等待程序
                         -> 结果日志
                         -> 客户端通知
```

## 服务生命周期

`waitctl start`、`waitctl goal` 和 `waitctl loop` 会按需启动服务。手动管理命令如下：

```bash
python src/waitctl.py daemon start
python src/waitctl.py daemon status
python src/waitctl.py list
python src/waitctl.py show WATCH_ID
python src/waitctl.py cancel WATCH_ID
python src/waitctl.py daemon stop
```

Unix 控制 socket 位于 `/tmp/wait-skill-<uid>/waitd.sock`。目录权限为 `0700`，注册表权限为 `0600`。协议版本和源码指纹用于阻止旧服务静默运行新代码。注册表保留活动 watcher、等待确认的 loop 计时器，以及其他最近完成的 256 条记录。

## 启动 watcher

`waitctl start` 有两层参数边界：

```text
waitctl.py start -- <waitd 参数> -- <等待程序及其参数>
```

第一个 `--` 结束 `waitctl` 参数解析，第二个把 watcher 参数与等待程序分开。`--ready`、`--terminal` 等旧状态匹配参数不属于 watcher；应把判断逻辑写入等待程序，通常使用 `wait_for.poll`。

可直接复制的 Codex 示例：

```bash
python src/waitctl.py start -- \
  --label "deployment api" \
  --event-note "复查部署；成功则检查健康，失败则报告原因" \
  --client codex \
  --session "$CODEX_THREAD_ID" \
  --remote "unix:///path/to/app-server-control.sock" \
  --timeout 3600 \
  -- env PYTHONPATH="$PWD/src" python /tmp/wait_deploy.py
```

返回成功前，`waitctl` 会确认：

- 服务已接收 watcher，且可以通过 `show` 查询；
- 活动 watcher 正持有 lock；
- 等待程序已经成功启动；
- 通知通道具有明确状态；
- watcher 具有有限截止时间。

成功响应分别展示这些状态：

```json
{
  "watcher": "active",
  "query": "verified",
  "delivery": "ready",
  "session": "<thread-id>",
  "deadline": 1700000000.0
}
```

Codex 的 `ready` 表示 Unix app-server 端点已完成 WebSocket 升级；显式 WebSocket 端点显示为 `configured`。Claude Code 的 `native_required` 表示 Root 仍需挂接原生后台 `follow` 任务。不绑定会话时自动投递为 `disabled`。

如果五秒内无法验证启动，`waitctl` 会取消 watcher 并以非零码退出。程序无法启动时同样返回非零。诊断日志和终态注册记录会保留，lock 会释放；这样既保留失败证据，也不会留下仍处于活动状态的半启动 watcher。

## 程序与结果契约

等待程序直接执行，不隐式调用 shell。协调路径相对提交目录解析，第二个 `--` 后的程序参数保持原样。独立 wait 默认生成日志和锁路径，也可以显式指定。取消或超时会终止整个程序进程组。

正常执行产生 `exited`、`timeout` 或 `start_failed`；服务重启后无法确认已运行程序的结果时产生 `interrupted`。这些结果保留退出码以及最多各 64 KiB 的 stdout、stderr。服务不解释业务状态，Agent 必须读取日志并重新检查外部状态。

持久化或收尾异常产生 `watcher_failed`，其中包含异常类型、repr 和 traceback。服务先保存程序结果，再更新 registry；如果后一步失败，正式结果日志不会被覆盖，registry 会在 `durable_result` 中携带已保存结果。

服务生成的唤醒消息为：

```text
$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"
```

Goal 和 loop 变体还会携带各自的状态标识。`event_note` 来自提交等待的 Agent，不从程序输出生成。

## 通知状态

Codex 会在程序启动前预检通知端点。Unix 端点缺失或不兼容时返回 `notification_unavailable` 和 `query_status: not_started`，且不创建 watcher。投递时，`codex queue` 的 stdout 和 stderr 同样各限制为 64 KiB。连接或协议故障直接变为 `notification_unavailable`，不重试；其他拒绝按配置的有界策略重试。

Claude Code 在结果持久化后进入 `native_pending`，所属对话按[客户端投递](clients.zh-CN.md)中的方法通过 `waitctl follow` 接收事件。

## Goal 与 loop

状态命令通过同一个服务执行：

```bash
python src/waitctl.py goal -- init --objective "CI 通过后发布" --session "$SESSION_ID"
python src/waitctl.py goal -- check --state "$GOAL_STATE"
python src/waitctl.py loop -- init \
  --task "检查队列" --interval 600 --duration 3600 \
  --event-note "读取计时事件并开始下一轮检查" \
  --session "$SESSION_ID"
```

同类状态命令串行执行，每条最长 30 秒；持久状态文件始终是事实来源。服务在执行 loop 计时器前记录注册意图，因此重启恢复可以补齐遗漏的注册，而不会重复迭代。

## 重启与恢复

Registry、状态和结果日志都通过唯一临时文件、fsync 和原子替换写入；瞬时 `ENOENT` 会做三次有界尝试。服务重启时为每个活动 watcher 记录 `recovered_at` 和 `recovery_action`，然后按已持久化的阶段恢复。

尚未启动的程序可以继续启动。处于 `running` 或 `querying` 的 watcher 会先查找 event ID 匹配的结果日志：找到后沿用原 event ID 和截止时间继续收尾与投递，找不到才标记为 `interrupted`，不会重放可能已经产生外部影响的程序。投递曾开始但无法确认结果时标记为 `unconfirmed`。

watcher 进入终态后释放 lock；重复 event ID 和重复 lock 所有权都会被拒绝。Goal 检查把受影响节点报告为 `orphaned_wait`，交由 Root 恢复。恢复流程只重建等待和投递状态，不授予重试外部修改的权限。

从已移除的状态匹配 CLI 迁移时，先检查并取消废弃 watcher，再把 `--ready` 和 `--terminal` 改写成调用 `wait_for.poll(query, evaluate)` 的等待脚本。
