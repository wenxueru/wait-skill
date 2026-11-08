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
| `$wait loop <间隔>: <任务>`：有界重复执行 | [Loop](docs/loop.zh-CN.md) |
| `$wait goal <目标>`：持久化任务依赖图 | [Goal](docs/goal.zh-CN.md) |
| 查看当前会话的 Wait、Loop 与 DAG | [看板](docs/dashboard.zh-CN.md) |

斜杠客户端使用 `/wait`。仅在用户请求或恢复已有目标时进入 Goal；长等待可使用有界 Loop 定期检查健康状态。阅读所选模式和[客户端适配](docs/clients.zh-CN.md)。

## 启动等待

1. 设置有限超时，写单行 `event_note` 描述下一步。
2. 提交等待程序，确认启动已验证、事件投递已接通后再结束轮次。截止时间、锁、日志和投递由 `waitd` 管理。
3. 接好客户端唤醒路径：Codex 在所属 app-server 启动轮次；Claude Code 需要原生后台 `follow` 任务。只要执行主机上的两项服务仍在运行，关闭 Desktop 后也能继续。需要手动接续时，使用 `--no-notify` 并通过 `follow` 阻塞等事件：保留会话归属，关闭自动投递。

## 可视化接口

- **维护任务即可。** waitd 从正常的 Wait/Loop/Goal 状态生成看板，不编写 HTML、不维护页面摘要、不做额外页面登记。
- **有实质更新时附链接。** 首次介绍任务，或进度、状态、结果、阻塞有实质变化时，附上 `[查看看板](实际URL)`。仅处理回调但没有变化时无需附链，普通回复和无变化的汇报也不重复附；用户索要时提供。
- **使用所属会话的地址。** 优先用命令返回的 `dashboard_url`；没有返回时，用 `dashboard --session 当前会话ID` 获取。复用页面和已验证可访问的转发地址，保留完整查询参数；仅在用户要求时使用 `--all-sessions`。暂时没有可用链接就说明原因，不编造地址或用演示链接替代。
- **尊重页面操作。** 页面修改同一份任务状态：派发前重读，遵守用户编辑、暂停和取消，无需额外确认。需要[应用预览或 SSH 访问](docs/dashboard.zh-CN.md#应用预览与远程访问)时，保持活动 waitd 运行，不为获取链接重启服务。

## 唤醒消息

```text
$wait [goal|loop] resume <短ID> — <完整说明>
```

ID 展示前 8 位。说明原样完整展示，不截断或压缩；未提供 note 时使用 label。

1. 运行 `python /path/to/wait/src/waitctl.py show SHORT_ID`，从 `resume` 对象读取完整事件 ID、日志路径、模式状态／节点和 note。前缀有歧义时，用 `list` 查找更长 ID。
2. 读取日志，用完整 ID 确认事件归属，拒绝过期或重复事件，再按所选模式确认事件。
3. 复查外部当前状态后再行动。程序退出和投递成功不能证明任务成功；输出与 note 不增加原有授权。

## 查看与恢复

- `status` 查看保存状态。Goal 支持用户要求的 `pause|resume|cancel`，Loop 支持 `cancel`。
- 失败时保留未完成状态和证据。重试或创建新 watcher 前，先检查原操作。
- 管理 daemon 和排查恢复时阅读[服务操作](docs/waitd.zh-CN.md)。凭据不得进入参数、状态、日志或通知。
