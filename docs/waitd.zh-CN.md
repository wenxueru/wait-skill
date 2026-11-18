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

Wait 先保存结果日志，再写入登记记录。等待任务失败时，可通过 `durable_result` 找到已保存的结果。事件字段及输出上限见[结果约定](wait.zh-CN.md#结果)。

重启后，活动记录会带有 `recovered_at` 和 `recovery_action`。Wait 按保存的阶段恢复。

| 保存的状态 | 恢复方式 |
| --- | --- |
| 程序未启动 | 在原截止时间内启动。 |
| 程序曾运行，且找到匹配的结果日志 | 使用已保存的结果收尾或继续投递原事件。 |
| 程序已启动，但结果缺失 | 记录 `interrupted`，供人工核对。 |
| 事件待投递、会话忙或端点离线 | 按保存的结果和重试时间继续投递。 |
| 投递处于 `attempting` | 记录 `unconfirmed`。再次唤醒前先核对原会话。 |
| 投递已接受 | 继续收尾或等待 Goal 确认。 |
| Loop 保存了下一轮，但计时器缺失 | 使用保存的等待 ID 恢复计时器。 |

已结束的等待会释放锁。Goal 的 `check` 若报告 `orphaned_wait`，请恢复[原节点](goal.zh-CN.md#恢复)。

## 等待任务故障

已保存的 `watcher_failed` 按[客户端投递规则](clients.zh-CN.md)送达。若投递记录保存失败，Wait 将存储错误写入服务日志，并在内存中保留结果。Claude Code 已连接的 `follow` 仍可读取。外部唤醒须先保存投递记录。

用 `show SHORT_ID` 和原会话核对结果及投递证据，再按[唤醒恢复步骤](../SKILL.zh-CN.md#唤醒消息)继续。
