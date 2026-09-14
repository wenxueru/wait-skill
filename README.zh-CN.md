# wait-skill

[English](README.md) | 简体中文

为 Codex 提供两个事件驱动能力：

- `$wait`：被动等待一个外部状态，不占用模型轮次反复轮询。
- `$wait-goal`：通过持久化依赖图执行多步骤目标，协调独立 Agent、外部等待和最终验收。

两者都以减少主动轮询和 Token 消耗为目标。`$wait` 可以独立使用，也可以作为 `$wait-goal` 中外部节点的等待机制。

## 安装

将此目录复制或克隆到 Codex 技能目录：

```bash
git clone https://github.com/wenxueru/wait-skill.git ~/.codex/skills/wait-goal
ln -s ~/.codex/skills/wait-goal/wait ~/.codex/skills/wait
```

仓库根目录注册 `$wait-goal`，`wait/` 子目录注册 `$wait`。创建符号链接后重启 Codex，使其发现并加载两个技能。

## 如何选择

| 场景 | 使用 |
| --- | --- |
| 等待一次部署、CI、队列或服务状态变化 | `$wait` |
| 任务包含多个步骤或依赖关系 | `$wait-goal` |
| 需要并行调度多个独立 Agent | `$wait-goal` |
| 完成所有步骤后还要验证原始需求 | `$wait-goal` |
| 目标执行到一半需要等待外部系统 | `$wait-goal` 调用 `$wait` |

## `$wait`：被动等待外部状态

使用 `$wait` 描述要监视的对象、就绪状态和终止状态：

```text
$wait 等待 deployment api 进入 Ready；如果进入 Failed 则停止。
```

`$wait` 把查询交给普通本地 Python 进程。等待期间 Codex 不需要持续占用模型轮次；监视器只在状态就绪、进入终止状态、超时或连续查询失败时唤醒现有任务。

底层 watcher 也可以直接运行：

```bash
python scripts/wait_for.py \
  --label "deployment api" \
  --ready Ready \
  --terminal Failed \
  --interval 60 \
  --thread "$CODEX_THREAD_ID" \
  --lock-file /tmp/wait-deployment-api.lock \
  --log-file /tmp/wait-deployment-api.json \
  --message-template '$wait resume /tmp/wait-deployment-api.json; event_id={event_id}; event={event}; status={status}. 恢复后先重新检查外部状态。' \
  -- deployctl status api --output status
```

查询命令是 `--` 后面的全部内容，并且不会隐式调用 shell。JSON 输出可以通过 `--json-path` 选择状态字段。省略 `--timeout` 表示无限等待；默认查询间隔为五分钟，默认连续失败上限为 12。

完整流程、安全边界和 CLI 参数见 [docs/wait.zh-CN.md](docs/wait.zh-CN.md)。

## `$wait-goal`：执行持久目标

使用 `$wait-goal` 启动包含依赖步骤的完整目标，不依赖原生 `/goal`：

```text
$wait-goal 发布 API，仅在测试和健康检查均通过后结束。
```

`$wait-goal` 会：

- 把目标拆成有向无环依赖图，并将状态保存到 `.wait-goal/`。
- 原子记录只追加的操作历史，并用明确的输入、产物以及只读或写入范围描述节点契约。
- 只调度依赖已完成且写入互不冲突的节点，并尽可能保持子任务相互独立。
- 由根 Agent 统一修改依赖图和接收子 Agent 结果。
- 没有可执行节点时结束模型轮次，不主动轮询不变的状态。
- 对外部节点使用 `$wait`，收到带 event ID 的事件后恢复调度。
- 所有节点完成后再次验证原始需求，通过后才结束目标。

完整的状态模型、依赖图规则、恢复流程和 CLI 参数见 [docs/wait-goal.zh-CN.md](docs/wait-goal.zh-CN.md)。

外部节点的组合示例：

```bash
python scripts/wait_goal.py wait \
  --state .wait-goal/release.json \
  --id deploy \
  --label "deployment api" \
  --log-file /tmp/wait-deployment-api.json \
  --lock-file /tmp/wait-deployment-api.lock \
  --startup-file /tmp/wait-deployment-api.started.json
```

命令会返回 `watch_id` 和规范化后的绝对协调路径。将这些值连同节点 ID 传给 watcher；确认启动回执并执行 `activate-wait` 后，`$wait` 才开始监视外部状态，并通过 `$wait-goal resume` 事件唤醒目标。完整命令见上方链接的指南。

## 安全模型

- 查询命令应当是只读的。
- 将凭据放在环境变量或配置文件中，不要放入命令行参数。
- 原始查询的标准输出和标准错误永远不会被转发或打印。
- `$wait` 会在通知前持久化 event ID 和投递状态；确定失败和结果不明的超时都会使用同一 ID 重试，因此可以安全去重重复唤醒。
- 唤醒消息并不授权重新启动或修改被监视的系统。接收消息的智能体必须重新确认当前状态及已有权限。
- `$wait-goal` 的依赖图必须保持无环，并且只有根 Agent 可以修改。

## 相关工作

`$wait-goal` 是面向 Codex 独立实现的运行时，没有复制以下项目的代码。以下工作直接影响了它的设计：

| 工作 | 相关思想 | 与 `$wait-goal` 的关系 |
| --- | --- | --- |
| [MACU](https://arxiv.org/abs/2606.01533)（[代码](https://github.com/kohjingyu/multi-agent-computer-use)） | Manager 构造和修改 DAG，并行派发 ready frontier，同时记录图快照和重新规划事件。 | 最接近的架构来源：中心调度、动态 DAG、受限 fan-out 和持久图历史。`$wait-goal` 还专门处理跨轮次休眠和外部事件唤醒。 |
| [DynTaskMAS](https://arxiv.org/abs/2503.07675) | 异步执行引擎释放依赖已完成的任务，并选择性传递上下文。 | 启发了 ready frontier 调度和依赖范围内的节点输入。`$wait-goal` 不允许子 Agent 直接共享上下文，所有信息仍经根 Agent 中转。 |
| [Atomic Task Graph](https://arxiv.org/abs/2607.01942) | 显式任务接口和图演化历史。 | 启发了节点输入/产物契约、只追加事件和重试记录保留。 |

## 开发

此脚本仅使用 Python 标准库。

```bash
python -m unittest discover -s tests -v
python /path/to/skill-creator/scripts/quick_validate.py .
```

## 许可证

MIT
