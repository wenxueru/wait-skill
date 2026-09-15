# wait-skill

[English](README.md) | 简体中文

为 CodeWiz、Cursor、Claude Code、GitHub Copilot 和 Codex 提供事件驱动等待：

- `$wait`：被动等待一个外部状态，不占用模型轮次反复轮询。
- `$wait-loop`：由用户主动调用的 Loop 模式实现，通过有界计时器重复执行一个任务。
- `$wait-goal`：用户主动调用的 Goal 模式实现，使用持久 DAG、中心化 Agent 调度、外部等待和最终验收。

`wait` 是主 Skill。衍生的 `wait-loop` 和 `wait-goal` 会复用它，但都不是 `wait` 的升级版或长程版，只在用户明确调用时启用。

## 安装

把仓库以 `wait` 名称克隆到当前客户端的 Skill 目录，再把内层衍生 Skill 暴露为同级 Skill：

```bash
skill_dir="$HOME/.codex/skills" # 替换为当前客户端的 Skill 目录
git clone https://github.com/wenxueru/wait-skill.git "$skill_dir/wait"
ln -s "$skill_dir/wait/wait-goal" "$skill_dir/wait-goal"
ln -s "$skill_dir/wait/wait-loop" "$skill_dir/wait-loop"
```

仓库根目录注册 `wait`，`wait-loop/` 和 `wait-goal/` 注册衍生 Skill。安装后重启或重新加载客户端。通过 SkillHub 安装时，可由它选择客户端对应的目录。

## 如何选择

| 场景 | 使用 |
| --- | --- |
| 等待一次部署、CI、队列或服务状态变化 | `$wait` |
| 用户明确调用 Loop 模式 | `$wait-loop` |
| 用户明确调用 Goal 模式 | `$wait-goal` |
| 正在运行的 goal 进入外部等待 | `$wait-goal` 把该等待交给 `$wait` |

不得从重复任务措辞推断 `$wait-loop`，也不得根据任务长度、依赖数量或 Agent 数量推断 `$wait-goal`。用户没有明确调用时，正常处理任务；只有确实需要被动监视一个外部状态时才使用 `$wait`。

## `$wait`：被动等待外部状态

使用 `$wait` 描述要监视的对象、就绪状态和终止状态：

```text
$wait 等待 deployment api 进入 Ready；如果进入 Failed 则停止。
```

`$wait` 把查询交给普通本地 Python 进程。等待期间客户端不需要持续占用模型轮次；监视器只在状态就绪、进入终止状态、超时或连续查询失败时恢复现有会话。

底层 watcher 也可以直接运行：

```bash
python scripts/wait_for.py \
  --label "deployment api" \
  --ready Ready \
  --terminal Failed \
  --interval 60 \
  --timeout 3600 \
  --client codex \
  --session "$AGENT_SESSION_ID" \
  --lock-file /tmp/wait-deployment-api.lock \
  --log-file /tmp/wait-deployment-api.json \
  --message-template '$wait resume /tmp/wait-deployment-api.json; event_id={event_id}; event={event}; status={status}. 恢复后先重新检查外部状态。' \
  -- deployctl status api --output status
```

查询命令是 `--` 后面的全部内容，并且不会隐式调用 shell。JSON 对象或数组必须使用 `--json-path` 选择标量状态。每次 wait 都有有限总时长：`--timeout` 默认为 24 小时。默认查询间隔为五分钟；连续失败上限默认为 12，且不能关闭。

完整流程、安全边界和 CLI 参数见 [docs/wait.zh-CN.md](docs/wait.zh-CN.md)。

## `$wait-loop`：显式 Loop 模式

```text
$wait-loop 每 10 分钟检查队列，并报告需要处理的变化。
```

第一轮立即执行；成功后只安排一个有界 `$wait` 计时器来触发下一轮。持久 event ID 防止重复唤醒造成重复执行，总时长和可选轮数上限避免循环泄漏。执行协议和完整示例见 [docs/wait-loop.zh-CN.md](docs/wait-loop.zh-CN.md)。

## `$wait-goal`：显式 Goal 模式

只有用户明确要求这种 Goal 模式时才使用 `$wait-goal`：

```text
$wait-goal 发布 API，仅在测试和健康检查均通过后结束。
```

`$wait-goal` 会：

- 把目标拆成有向无环依赖图；默认将每个目标保存到 `/tmp/.wait-goal/<项目名>-<路径哈希>/` 下的唯一文件，`--state` 可以覆盖该路径。
- 原子记录只追加的操作历史，并用明确的输入、产物以及只读或写入范围描述节点契约。
- 只调度依赖已完成且写入互不冲突的节点，并尽可能保持子任务相互独立。
- 由根 Agent 统一修改依赖图和接收子 Agent 结果。
- 没有可执行节点时结束模型轮次，不主动轮询不变的状态。
- 对外部节点使用 `$wait`，收到带 event ID 的事件后恢复调度。
- 所有节点完成后再次验证原始需求，通过后才结束目标。

完整的状态模型、依赖图规则、恢复流程和 CLI 参数见 [docs/wait-goal.zh-CN.md](docs/wait-goal.zh-CN.md)。

外部节点的组合示例：

```bash
# wait_goal.py init 返回的 state_file
GOAL_STATE=/tmp/.wait-goal/PROJECT/GOAL.json

python scripts/wait_goal.py wait \
  --state "$GOAL_STATE" \
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
- `$wait` 会在通知前持久化 event ID 和投递状态；有限次数的重试复用同一 ID，因此可以安全去重重复唤醒。
- 唤醒消息并不授权重新启动或修改被监视的系统。接收消息的智能体必须重新确认当前状态及已有权限。
- `$wait-goal` 的依赖图必须保持无环，并且只有根 Agent 可以修改。

## 相关工作

`$wait-goal` 是独立实现，没有复制以下项目的代码。这里只记录直接影响实现的工作：

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
