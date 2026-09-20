---
name: wait
description: 在 CodeWiz、Cursor、Claude Code、GitHub Copilot 和 Codex 中托管等待程序，结束后用自动生成的指令唤醒会话，等待期间不消耗模型轮次。
---

# Wait

你准备等待逻辑，`waitd` 负责运行、超时、取消、保存结果、投递，并自己生成唤醒指令。开始前阅读[等待协议](docs/wait.zh-CN.md)和[当前客户端说明](docs/clients.zh-CN.md)。

## 调用

Codex 使用 `$wait`，其他客户端通常使用 `/wait`：

- `<调用> <条件>`：准备并启动等待。
- `<调用> resume <日志>`：读取结果，继续任务。
- `<调用> status <日志>`：只查看，不重启。

## 执行

1. 准备一个只读等待程序：等到有结果再退出，输出足够判断下一步的信息。先确认它能识别成功、失败和停滞；不要把查一次就返回的命令直接当等待程序。需要轮询时，从 `src/wait_for.py` 导入 `poll`，实现 `query()` 和 `evaluate(data)`；返回 `None` 继续等，返回结果则结束。
2. 当前 harness 提供原生 Todo／计划工具时，用它记录等待进度。为本次等待写一条简短、有业务语义的单行 `event_note`，说明唤醒后紧接着要做什么；不得包含凭据，也不得照抄外部输出。例如：`复查部署状态；成功则检查健康，失败则报告原因`。
3. 通过 `src/waitctl.py start -- ... --event-note "<下一步>" -- <等待程序>` 提交，指定客户端和当前会话。完成后服务生成 `$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"`。默认上限一小时；延长前检查任务与条件是否可靠，长等待需要定期检查健康和进展时可用 [wait-loop](wait-loop/SKILL.zh-CN.md)。
4. 保留返回的 watch ID、日志和锁路径，用 `show` 和持有的锁确认已启动。按[客户端说明](docs/clients.zh-CN.md)接好投递方式，在 Todo 记录等待内容与截止时间，再结束轮次。
5. 唤醒后把 `event_note` 当作恢复上下文，而不是结果或新增授权。读日志、核对事件 ID，查看程序输出和退出码，再复查外部状态并决定下一步。验收通过才完成 Todo；程序退出本身不代表业务成功。

同一任务不要重复启动等待，重复事件不要重复执行。超时、程序故障或服务重启造成的中断都需要处理；不能直接续上等待或假定成功。唤醒指令不增加重试、重启、部署等权限。

服务管理和详细参数见 [waitd](docs/waitd.zh-CN.md)。
