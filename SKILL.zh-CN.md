---
name: wait
description: 在 Codex 或 Claude Code 中被动等待一个由外部命令报告的状态，并在状态变化后唤醒所属会话，不消耗模型轮询。
---

# Wait

当任务依赖一个外部条件，并且可以用有界、只读的程序判断条件是否变化时使用 `wait`。`waitd` 负责程序生命周期、超时、结果日志和唤醒投递；Agent 负责查询逻辑、恢复判断和最终验收。

创建 watcher 前阅读[等待协议](docs/wait.zh-CN.md)，选择投递方式前阅读[客户端说明](docs/clients.zh-CN.md)。

## 调用

- Codex 用 `$wait <条件>` 启动等待；Claude Code 通常用 `/wait`。
- `$wait resume <日志>` 处理已投递事件。
- `$wait status <日志>` 只检查保存的结果，不重启等待。

## 启动等待

1. 编写只读等待程序，让它持续运行到能够报告有效结果。程序必须区分成功、失败和停滞。需要轮询时使用 `src/wait_for.py` 的 `poll(query, evaluate)`：返回 `None` 继续，返回结果结束。
2. 写一条简洁、有语义的单行 `event_note`，说明唤醒后紧接着做什么。它由 Agent 编写，只是恢复上下文，不是程序输出、事实证据或新增授权。
3. 使用有限超时提交：

   ```bash
   python src/waitctl.py start -- \
     --label "<简短名称>" \
     --event-note "<下一步动作>" \
     --client <客户端> --session <会话 ID> \
     -- <等待程序>
   ```

4. 只接受完成启动验证的响应：`watcher` 为 active，`query` 为 verified，`delivery` 与所选客户端一致。保存 watch ID、日志、锁路径和截止时间，然后结束本轮。当前环境提供 Todo 或计划工具时同步记录。

Codex 会在查询开始前检查通知端点；没有兼容的 app-server 端点时立即返回 `notification_unavailable`，且不创建 watcher。Claude Code 通过原生后台 `follow` 命令接收事件。具体配置见[客户端说明](docs/clients.zh-CN.md)。

## 唤醒与验收

服务发送：

```text
$wait resume {log_file}; event_id={event_id}; event_note="{event_note}"
```

收到后依次执行：

1. 用 event ID 对照原 watcher，拒绝重复或过期事件。
2. 读取日志，检查进程事件、退出码、stdout 和 stderr。
3. 重新查询外部状态；程序结束不等于业务成功。
4. 只在原任务与原授权范围内执行 `event_note` 指向的下一步。
5. 验收通过后再把进度标为完成。

查询、投递或服务失败时，不要因为失败本身就创建替代 watcher。保留原日志，如实报告 `timeout`、`start_failed`、`interrupted`、`notification_unavailable` 或 `unconfirmed`，核对当前状态后再恢复。

需要按固定间隔重复执行任务时使用 [wait-loop](wait-loop/SKILL.zh-CN.md)，不要把多个周期塞进一次 wait。服务参数和恢复规则见 [waitd](docs/waitd.zh-CN.md)。
