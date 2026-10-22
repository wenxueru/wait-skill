# `waitd`：服务操作

[English](waitd.md)

`waitctl` 连接一个本地 `waitd`，后者管理 watcher 进程、期限、锁、日志和投递。Goal、Loop 状态命令也走同一服务，同类命令串行，单条限时 30 秒。Agent 负责查询逻辑与验收。

## 服务生命周期

`start`、`goal`、`loop` 按需启动服务，也可手动管理：

```bash
python /path/to/wait/src/waitctl.py daemon start
python /path/to/wait/src/waitctl.py daemon status
python /path/to/wait/src/waitctl.py list
python /path/to/wait/src/waitctl.py show WATCH_ID
python /path/to/wait/src/waitctl.py cancel WATCH_ID
python /path/to/wait/src/waitctl.py daemon stop
```

Unix socket 是 `/tmp/wait-skill-<uid>/waitd.sock`，目录权限 `0700`、registry 权限 `0600`。协议／源码指纹不匹配时返回错误，不停止现有 daemon；切换版本前应等活动 watcher 结束，再显式停止。Registry 保留活动 watcher、等待确认的 Loop 计时器和最近 256 个其他终态记录。

## 提交 watcher

```text
waitctl.py start -- <watcher 参数> -- <等待程序及参数>
```

客户端返回成功前验证记录可查、活动 watcher 已持锁、程序已启动、投递状态明确且期限有限。五秒内无法验证启动会取消；很快的程序可能在响应前结束。无法创建程序时保留终态记录和诊断日志，并释放锁。完整提交示例见 [Wait 流程](wait.zh-CN.md#流程)。

| 响应字段 | 含义 |
| --- | --- |
| `watch_id`、`log_file`、`lock_file` | 持久标识与证据；普通 wait 未覆盖时自动生成 |
| `watcher` | Watcher 当前生命周期状态 |
| `query: verified` | 已观察到程序启动 |
| `delivery` | 客户端投递就绪状态，不代表业务验收，见[客户端](clients.zh-CN.md) |
| `session`、`deadline` | 所属会话与有限程序截止时间 |

协调路径以提交者目录解析；内层 `--` 后的 argv 原样直接执行，不隐式调用 shell。超时／取消会终止整个进程组。旧 `--ready` / `--terminal` 状态匹配应写在[等待程序](wait.zh-CN.md#等待程序约定)里，不再作为 watcher 参数。

## 持久化与恢复

状态、registry 和结果均用唯一临时文件、fsync 和原子替换写入；瞬时 `ENOENT` 最多重试三次。程序结果先于 registry 落盘，后续 watcher 故障保留该日志，并在可能时通过 `durable_result` 引用。事件含义与输出上限见[结果约定](wait.zh-CN.md#结果)。

重启时活动记录增加 `recovered_at`、`recovery_action`，按保存的阶段恢复：

| 证据 | 动作 |
| --- | --- |
| 程序未启动 | 可在原期限内启动一次 |
| `running` / `querying` 且结果日志匹配 | 用原 event ID 继续收尾／投递 |
| 已启动但没有持久结果 | 记录 `interrupted`，不重放不确定执行 |
| 投递处于 `attempting` | 记录 `unconfirmed`，重试前检查原会话 |
| 投递已接受 | 继续收尾／确认，不再次发送 |
| Loop 状态已提交但缺少计时器注册 | 用保存的 watch ID 恢复计时器，不重跑迭代 |

终态 watcher 释放锁，重复 event ID 和锁冲突会被拒绝。Goal `check` 将失去 watcher 所有权报告为 `orphaned_wait`，应恢复[原节点](wait-goal.zh-CN.md#恢复)。恢复只重建调度与投递，不增加外部修改权限。

## Watcher 故障

`watcher_failed` 使用普通[客户端投递](clients.zh-CN.md)。投递前持久化失效时，daemon 记录服务日志，再尝试一次带 `event=watcher_failed; persistence=unavailable` 的唤醒，或通知已连接的 Claude `follow`。该唤醒及内存状态不持久，重启后可能重复；持久证据显示可能已投递成功时不会再次发送。

唤醒消息显示短 event ID 和 Agent 编写的完整 `event_note`，路径通过 `show SHORT_ID` 读取，从不嵌入原始程序输出。持久化失败后日志与 registry 均可能过期，决定恢复方式前先检查证据。

先按[唤醒恢复协议](../SKILL.zh-CN.md#唤醒消息)解析短 ID，再执行本模式的恢复步骤。
