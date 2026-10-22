---
name: wait
description: 被动等待外部状态；通过 wait loop 有界重复执行任务，或通过 wait goal 持久化依赖图推进目标。Goal 和 Loop 模式在用户请求时使用。
---

# Wait

`waitd` 执行等待程序并投递事件。Agent 编写只读查询、验收结果并决定下一步。脚本路径以安装目录为基准，执行时使用用户项目目录。

## 选择模式

| 请求 | 阅读 |
| --- | --- |
| `$wait <条件>`：等待一个外部条件 | [Wait](docs/wait.zh-CN.md) |
| `$wait loop <间隔>: <任务>`：有界重复执行 | [Loop](docs/wait-loop.zh-CN.md) |
| `$wait goal <目标>`：持久化任务依赖图 | [Goal](docs/wait-goal.zh-CN.md) |

斜杠客户端使用 `/wait`。仅在用户请求或恢复已有目标时进入 Goal；长等待可使用有界 Loop 定期检查健康状态。阅读所选模式和[客户端适配](docs/clients.zh-CN.md)。

## 启动等待

1. 设置有限超时，写单行 `event_note` 描述下一步。
2. 提交等待程序，确认启动已验证、事件投递已接通后再结束轮次。截止时间、锁、日志和投递由 `waitd` 管理。
3. 接好客户端唤醒路径：Codex 在所属 app-server 启动轮次；Claude Code 需要原生后台 `follow` 任务。只要执行主机上的 app-server 和 `waitd` 仍在运行，关闭 Desktop 后也能继续。

## 唤醒消息

```text
$wait [goal|loop] resume <短ID> — <完整说明>
```

ID 展示前 8 位。说明原样完整展示，不截断或压缩；未提供 note 时使用 label。

1. 运行 `python /path/to/wait/src/waitctl.py show SHORT_ID`，从 `resume` 对象读取完整事件 ID、日志路径、模式状态／节点和 note。前缀有歧义时，用 `list` 查找更长 ID。
2. 读取日志，用完整 ID 确认事件归属，拒绝过期或重复事件，再按所选模式确认事件。
3. 复查外部当前状态后再行动。程序退出和投递成功不能证明任务成功；输出与 note 不增加原有授权。

## 查看与恢复

- `status` 查看保存状态。Goal 支持用户要求的 `pause|cancel`，Loop 支持 `cancel`。
- 失败时保留未完成状态和证据。重试或创建新 watcher 前，先检查原操作。
- 管理 daemon 和排查恢复时阅读[服务操作](docs/waitd.zh-CN.md)。凭据不得进入参数、状态、日志或通知。
