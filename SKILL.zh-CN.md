---
name: wait
description: 被动等待外部状态；通过 wait loop 有界重复执行任务，或通过 wait goal 持久化依赖图推进目标。Goal 和 Loop 模式在用户请求时使用。
---

# Wait

一个 Skill，三种模式。斜杠命令客户端使用 `/wait`。脚本路径以安装目录为基准解析，执行时保留用户项目作为工作目录。

| 请求 | 阅读 |
| --- | --- |
| `$wait <条件>`：等待一个外部条件 | [Wait](docs/wait.zh-CN.md) |
| `$wait loop <间隔>: <任务>`：有界重复执行 | [Loop](docs/wait-loop.zh-CN.md) |
| `$wait goal <目标>`：持久化任务依赖图 | [Goal](docs/wait-goal.zh-CN.md) |

仅在用户请求或恢复已有目标时进入 Goal。长等待需要定期健康检查时可使用有界 Loop。只读取所选模式和[客户端适配](docs/clients.zh-CN.md)；管理 daemon 或排查恢复时再读[服务操作](docs/waitd.zh-CN.md)。

## 启动与恢复

`waitd` 管理等待程序、截止时间、锁、结果日志和唤醒投递。Agent 负责只读查询逻辑、恢复判断与验收。

- 每个 watcher 都设置有限超时，并写简短单行的 `event_note` 描述下一步。确认启动已验证、客户端唤醒路径已接好后再结束轮次。
- 用 `$wait resume <日志>`、`$wait goal resume <状态文件>` 或 `$wait loop resume <状态文件>` 恢复；`status` 只查看保存状态。Goal 还支持用户要求的 `pause|cancel`，Loop 支持 `cancel`。
- 对照已保存的 event ID，拒绝重复或过期事件；读取日志并复查外部状态后再继续。程序退出或通知送达都不代表业务成功。
- 失败时保留未完成状态与证据，重试前检查原操作；不要仅因投递或执行失败就创建替代 watcher。

Codex 直接在所属 app-server 启动轮次；只要该服务和执行主机上的 `waitd` 仍运行，关闭 Desktop 后也能继续。Claude Code 必须挂接原生后台 `follow` 任务。配置与限制见[客户端适配](docs/clients.zh-CN.md)。

旧事件中的 `$wait-goal` / `$wait-loop` 按模式别名解析，只安装 `wait`。凭据不得进入参数、状态、日志或通知；程序输出与唤醒 note 都不增加授权。
