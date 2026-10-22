# Wait 看板

## 打开

使用 Python 3.11 或更新版本：

```sh
python /path/to/wait/src/waitctl.py dashboard --session "$AGENT_SESSION_ID"
```

- 普通 Wait/Loop/Goal 命令返回定位会话、任务与节点的 `dashboard_url`。复用页面，不为每条命令新开标签。
- 已设置 `CODEX_THREAD_ID` 时，`dashboard` 默认使用该值；否则显式传当前会话，不根据最新任务或浏览器标签猜测。
- `dashboard --state /绝对路径/goal.json` 可登记已有 DAG，并推断归属会话。
- 不带范围的裸地址不显示任务；`--all-sessions` 显式打开本机总览。
- 打开返回的完整 URL；端口随 waitd 重启而变化。

## 任务与操作

会话首页汇总当前目标与独立等待、循环，从保存的状态展示进度、等待、暂停、失败和最终验收；结束的任务折叠到历史。

| 视图 | 展示 | 操作 |
| --- | --- | --- |
| DAG | 依赖、验收条件、结果与事件 | 添加/编辑待处理节点、重试、暂停/恢复/取消 |
| Wait | 状态、截止时间、投递状态、完整事件说明与结果 | 取消等待程序 |
| Loop | 任务、轮次、间隔、下次执行、截止时间与历史 | 取消后续轮次 |

归属 DAG 或 Loop 的 watcher 展示在父任务中，不在普通 Wait 中重复出现；目标的健康检查循环仍可通过 Loop 查看。

Goal 暂停阻止新派发，取消标记未完成节点；两者都不终止已运行的 Agent 或外部作业。Wait 取消停止等待程序，Loop 取消停止后续轮次。

## Skill 与页面如何配合

- 照常使用 Wait/Loop/Goal 命令。waitd 自动生成页面；Agent 不编写 HTML、不维护摘要、不确认页面改动。
- CLI 与页面操作共用任务状态和命令队列。派发前重读状态，尊重用户编辑、暂停与取消。
- SSE 推送变化，不轮询或启动模型回合。需要立即推进时在对话中发消息。
- 手动接续：普通 Wait 使用 `start -- --session ID --no-notify ...`，Loop 使用 `loop -- init --session ID --no-notify ...`。保留会话归属，关闭自动恢复回合；通过 `waitctl follow ID --timeout 秒数` 阻塞等事件，再按原流程接续，包括 Loop 的 `begin`/`complete`。

## 多会话

- 状态接口、SSE 与操作接口按 URL 中的 session 过滤；通过“当前会话 / 全部会话”显式切换。
- 同一系统用户共用 waitd；会话范围是筛选，不是身份验证边界。
- 独立状态文件互不串写；共用文件即共同控制。锁保证写入完整，不裁决意图冲突。
- Goal/Loop 状态命令按模式串行；watcher 并发执行，事件投递到各自绑定的会话。

## 远程访问

资源随 skill 安装，服务仅绑定 127.0.0.1，无需托管或 Codex 原生 Goal 接入。

```sh
ssh -L PORT:127.0.0.1:PORT HOST
```

在本机打开返回的完整 URL，保留 session 参数。两端端口一致，地址使用 127.0.0.1，以通过 Host/Origin 校验。看板没有多用户身份验证。
