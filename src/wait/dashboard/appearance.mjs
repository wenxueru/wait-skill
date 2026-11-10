import {timeDate} from "./models/tasks.mjs";

const $ = id => document.getElementById(id);

export function createAppearance(onLanguageChange) {
  const copy = {
    en: {
      "goal.description": "Full objective", "workspace.display": "Task display", "workspace.list": "Task list",
      "workspace.search": "Search tasks or IDs…", "workspace.filter": "Filter tasks", "workspace.all": "All tasks",
      "workspace.visibility": "Task visibility", "workspace.current": "Current tasks", "workspace.history": "History",
      "workspace.noCurrent": "No current tasks", "workspace.historyHint": "Finished tasks are in History.",
      "workspace.showHistory": "View history",
      "workspace.count": "{shown} / {total} tasks", "workspace.empty": "No matching tasks",
      "workspace.emptyHint": "Adjust the search or filters.", "workspace.task": "Task", "workspace.state": "Status",
      "workspace.emptyGoal": "No tasks yet", "workspace.addFirst": "Add a task to get started.",
      "workspace.parent": "Parent task", "workspace.dependencies": "Dependencies", "workspace.next": "Next step", "workspace.context": "Notes",
      "sort.desc": "{column}: sort descending", "sort.asc": "{column}: sort ascending", "sort.default": "{column}: restore default order", "state.ready": "Ready", "state.blocked": "Blocked",
      "details.title": "Task details", "details.dependencies": "Dependencies", "details.edit": "Edit task",
      "graph.fit": "Fit", "graph.zoomIn": "Zoom in", "graph.zoomOut": "Zoom out",
      "graph.helpLabel": "Map help", "graph.zoom": "Zoom", "graph.help": "Drag anywhere / scroll to pan · Click a node for details · Ctrl + scroll / pinch to zoom",
      "duration.title": "Duration", "duration.total": "Total elapsed", "duration.phase": "Current phase",
      "duration.pending": "Not started", "duration.running": "Running", "duration.waiting": "Waiting", "duration.dispatching": "Dispatching",
      "duration.day": "d", "duration.hour": "h", "duration.minute": "m", "duration.second": "s",
      "page.title": "Wait dashboard", "list.separator": ", ",
      "loop.every": "Every {duration}", "loop.failedTimers": "{count} failed", "loop.timers": "{count} timers", "loop.recent": "Recent iterations", "loop.timerHistory": "Timer history · {count}",
      "graph.relations": "Relationships", "activity.related": "Related tasks",
      "activity.emptyHint": "Create a task in chat.",
      "activity.open": "Open {kind}", "activity.parentUnavailable": "The parent task is not available in this session.",
      "activity.managed": "Managed by parent task.",
      "activity.relationsHint": "Lines show recorded ownership and current timers, plus known goal dependencies. Independent waits have no inferred connections.",
      "help.wait.running": "The watcher is running its read-only waiting program. This is not business-task execution or acceptance.",
      "help.wait.waiting": "The watcher is waiting to start, deliver an event, or receive acknowledgement. Inspect query and delivery details for its exact phase.",
      "help.loop.running": "The current loop iteration is executing. The next timer is scheduled only after this iteration is completed.",
      "help.loop.waiting": "The last iteration finished; the loop is waiting for its next timer and event handling.",
      "scope.current": "Current session", "scope.all": "All sessions", "scope.select": "Switch session", "scope.search": "Search session ID…", "scope.counts": "Waits {wait} · Loops {loop} · Goals {goal}", "scope.loadError": "Could not load sessions. Reopen to retry.",
      "settings.title": "Settings", "language.label": "Language", "theme.label": "Appearance",
      "time.updated": "Updated", "time.created": "Created", "time.started": "Started", "time.waiting": "Waiting since", "time.finished": "Ended",
      "theme.light": "Light", "theme.dark": "Dark",
      "connection.connecting": "Connecting", "connection.live": "● Live", "connection.reconnecting": "Reconnecting",
      "session.connecting": "Connecting to the session…",
      "status.attention": "Tasks need attention", "status.paused": "A goal is paused", "status.running": "Work in progress",
      "status.waiting": "Waiting for an event", "status.verification": "Final verification pending", "status.ready": "Ready to continue",
      "status.idle": "No active tasks", "status.start": "Ready when you are",
      "mode.goal": "Goals", "mode.wait": "Waits", "mode.loop": "Loops", "view.aria": "Task views",
      "select.goal": "Select a goal", "goal.pause": "Pause", "goal.resume": "Resume", "common.cancel": "Cancel",
      "empty.goals.title": "No goals yet", "empty.goals.body": "Create a goal in chat.",
      "graph.title": "Dependency map", "graph.add": "+ Node", "events.recent": "Recent changes",
      "state.open": "Active", "state.paused": "Paused", "state.completed": "Completed", "state.cancelled": "Cancelled",
      "state.pending": "Pending", "state.dispatching": "Dispatching", "state.running": "Running", "state.waiting": "Waiting", "state.failed": "Failed",
      "help.ready": "Dependencies are complete. The agent can check resources and write scope, then start this task.",
      "help.blocked": "One or more dependencies are unfinished. This task cannot start yet; it has not failed.",
      "help.pending": "This task has not started. It may be queued or held while the goal is paused.",
      "help.dispatching": "A subagent dispatch is reserved; its runtime ID has not been attached yet.",
      "help.running": "This task has started and is awaiting its work or result. This does not mean it has passed acceptance.",
      "help.waiting": "An external wait is registered. Its event still needs to arrive and be handled before work continues.",
      "help.completed": "This node's result was accepted. Other nodes or final goal verification may still remain.",
      "help.failed": "This attempt failed. Inspect the result and evidence, address the cause, then consider retrying.",
      "help.paused": "New dispatch is paused. Tasks and external jobs already running may continue.",
      "help.cancelled": "Further scheduling is cancelled. External jobs already started are not automatically terminated.",
      "help.goal.open": "The goal is open. Work follows dependencies; completion requires final verification of the original objective.",
      "help.goal.completed": "All nodes are complete, final verification is recorded, and the goal has finished.",
      "help.wait.active": "The watcher is active: it may be checking the condition, delivering an event, or awaiting acknowledgement.",
      "help.wait.completed": "The waiting program exited successfully. The agent must still verify the actual external state; this alone is not business success.",
      "help.wait.failed": "The waiting program, deadline, or event delivery failed. Inspect the detailed result.",
      "help.wait.cancelled": "Cancellation was requested; the watcher's managed waiting process will stop.",
      "help.loop.active": "The loop is active, either executing its current iteration or waiting for the next timer.",
      "help.loop.completed": "The iteration count or time limit was reached. No further iterations will be scheduled.",
      "help.loop.cancelled": "Future iterations are cancelled. Work already running is not forcibly stopped.",
      "event.init": "Goal created", "event.add_node": "Node added", "event.edit_node": "Node updated", "event.start_node": "Node started",
      "event.complete_node": "Node completed", "event.fail_node": "Node failed", "event.retry_node": "Node retried",
      "event.pause_goal": "Goal paused", "event.resume_goal": "Goal resumed", "event.cancel_goal": "Goal cancelled",
      "event.verify_goal": "Goal verified", "event.finish_goal": "Goal completed", "event.wake_node": "Event received", "event.unknown": "Change",
      "event.actor.user": "You", "event.actor.event": "Event", "event.actor.agent": "Agent",
      "notice.wait": "Cancellation requested. The waiting process will stop.",
      "notice.loop": "The loop is cancelled; a task already running will continue.",
      "notice.pause": "New work is paused; running tasks may continue.",
      "notice.cancel": "The goal is cancelled; external jobs will not be stopped.",
      "notice.saved": "Saved; applies on the next dispatch.",
      "confirm.wait": "Cancel this wait? The waiting process will stop.", "confirm.loop": "Cancel this loop? Future iterations will stop.",
      "confirm.goal": "Cancel this goal? Incomplete tasks will stop being scheduled.",
      "empty.wait": "No waits", "empty.loop": "No loops", "state.active": "Active",
      "common.add": "Add", "common.back": "Back", "common.details": "Details", "common.retry": "Retry node",
      "common.edit": "Save changes",
      "field.query": "Query {value}", "field.notification": "Notification {value}", "field.deadline": "Due {value}",
      "field.session": "Session {value}", "field.triggered": "On trigger · {value}", "field.next": "Next {value}",
      "field.iteration": "Iteration {value}", "field.interval": "Every {value}s",
      "field.loopNext": "Next · {value}", "field.loopState": "{task} · iteration {iteration} · {status}",
      "field.noDependencies": "None", "field.acceptance": "Acceptance criteria", "field.waitLoop": "Waits and loops",
      "field.externalCondition": "External condition", "field.result": "Result", "field.waitDetails": "Wait details",
      "field.selectNode": "Select a task", "field.nodeKind.local": "Main agent", "field.nodeKind.agent": "Subagent",
      "field.nodeKind.external": "External event", "field.completeCount": "{done} of {total} completed",
      "node.add": "Add node", "node.id": "Node ID", "node.title": "Task description", "node.kind": "Type",
      "node.kind.local": "Main agent", "node.kind.agent": "Subagent", "node.kind.external": "External event",
      "node.dependencies": "Dependencies (comma-separated IDs)", "node.acceptance": "Acceptance criteria (one per line)",
      "node.write_paths": "Write paths (one per line; leave blank for read-only)"
    },
    zh: {
      "goal.description": "查看完整目标", "workspace.display": "任务展示方式", "workspace.list": "任务列表",
      "workspace.search": "搜索任务或 ID…", "workspace.filter": "筛选任务", "workspace.all": "全部任务",
      "workspace.visibility": "任务显示范围", "workspace.current": "当前任务", "workspace.history": "历史",
      "workspace.noCurrent": "暂无当前任务", "workspace.historyHint": "已结束任务在历史中。",
      "workspace.showHistory": "查看历史",
      "workspace.count": "{shown} / {total} 个任务", "workspace.empty": "没有匹配的任务",
      "workspace.emptyHint": "试试其他关键词或筛选。", "workspace.task": "任务", "workspace.state": "状态",
      "workspace.emptyGoal": "还没有任务", "workspace.addFirst": "添加任务开始。",
      "workspace.parent": "所属任务", "workspace.dependencies": "依赖任务", "workspace.next": "下一步", "workspace.context": "说明",
      "sort.desc": "{column}：按降序排列", "sort.asc": "{column}：按升序排列", "sort.default": "{column}：恢复默认排序", "state.ready": "可推进", "state.blocked": "依赖未就绪",
      "details.title": "任务详情", "details.dependencies": "依赖任务", "details.edit": "编辑任务",
      "graph.fit": "适应窗口", "graph.zoomIn": "放大", "graph.zoomOut": "缩小",
      "graph.helpLabel": "图操作说明", "graph.zoom": "缩放", "graph.help": "任意位置拖动／滚轮平移 · 轻点节点查看详情 · Ctrl + 滚轮／触控板捏合缩放",
      "duration.title": "耗时", "duration.total": "总历时", "duration.phase": "当前阶段",
      "duration.pending": "尚未开始", "duration.running": "已执行", "duration.waiting": "已等待", "duration.dispatching": "分派中",
      "duration.day": "天", "duration.hour": "时", "duration.minute": "分", "duration.second": "秒",
      "page.title": "Wait · 看板", "list.separator": "、",
      "loop.every": "每 {duration}", "loop.failedTimers": "{count} 个失败", "loop.timers": "{count} 个计时器", "loop.recent": "最近轮次", "loop.timerHistory": "计时记录 · {count}",
      "graph.relations": "关联图", "activity.related": "关联任务",
      "activity.emptyHint": "在对话中创建任务。",
      "activity.open": "打开{kind}", "activity.parentUnavailable": "所属任务不在当前会话可用范围内。",
      "activity.managed": "由父任务管理。",
      "activity.relationsHint": "连线表示已记录的归属、当前计时器，以及已知的目标依赖。独立等待之间不会推测连线。",
      "help.wait.running": "等待器正在运行只读查询程序，不代表业务任务正在执行或已经验收。",
      "help.wait.waiting": "等待器正在等启动、事件投递或确认；可在详情查看查询和通知的具体阶段。",
      "help.loop.running": "正在执行当前轮次，完成本轮后才会安排下一个计时器。",
      "help.loop.waiting": "上一轮已结束，正在等下一个计时器及事件处理。",
      "scope.current": "当前会话", "scope.all": "全部会话", "scope.select": "切换会话", "scope.search": "搜索会话 ID…", "scope.counts": "{wait} 等待 · {loop} 循环 · {goal} 目标", "scope.loadError": "无法加载会话，请重新打开重试。",
      "settings.title": "设置", "language.label": "语言", "theme.label": "外观",
      "time.updated": "最近更新", "time.created": "创建时间", "time.started": "开始时间", "time.waiting": "开始等待", "time.finished": "结束时间",
      "theme.light": "浅色", "theme.dark": "深色",
      "connection.connecting": "连接中", "connection.live": "● 实时", "connection.reconnecting": "重连中",
      "session.connecting": "正在连接会话…",
      "status.attention": "有任务需要处理", "status.paused": "有目标已暂停", "status.running": "工作推进中",
      "status.waiting": "等待外部事件", "status.verification": "等待最终验收", "status.ready": "有任务可以推进",
      "status.idle": "当前没有待运行任务", "status.start": "准备开始",
      "mode.goal": "目标", "mode.wait": "等待", "mode.loop": "循环", "view.aria": "任务视图",
      "select.goal": "选择目标", "goal.pause": "暂停", "goal.resume": "恢复", "common.cancel": "取消",
      "empty.goals.title": "还没有目标", "empty.goals.body": "在对话中创建目标。",
      "graph.title": "依赖图", "graph.add": "+ 节点", "events.recent": "最近变更",
      "state.open": "进行中", "state.paused": "已暂停", "state.completed": "已完成", "state.cancelled": "已取消",
      "state.pending": "待处理", "state.dispatching": "分派中", "state.running": "执行中", "state.waiting": "等待事件", "state.failed": "失败",
      "help.ready": "依赖已经完成，Agent 可检查资源和写入范围后启动任务。尚未开始执行。",
      "help.blocked": "至少一个依赖尚未完成，暂时不能启动；这不代表任务失败。",
      "help.pending": "任务尚未开始，可能正在排队，或因目标暂停而暂不派发。",
      "help.dispatching": "已预留子 Agent 的分派，尚未关联实际运行实例。",
      "help.running": "任务已启动，正在执行工作或等待结果；不代表已通过验收。",
      "help.waiting": "已登记外部等待，需等事件到达并被处理后才能继续推进。",
      "help.completed": "该节点的结果已被接受；整个目标可能仍有其他任务或最终验收未完成。",
      "help.failed": "本次尝试失败。请查看结果与证据，处理原因后再考虑重试。",
      "help.paused": "暂停派发新任务；已经运行的任务和外部作业可能继续。",
      "help.cancelled": "已取消后续调度；已经启动的外部作业不会因此自动终止。",
      "help.goal.open": "目标尚未结束，按依赖关系推进；完成前需要对原始目标进行最终验收。",
      "help.goal.completed": "全部节点已完成，原始目标的最终验收已记录，目标已结束。",
      "help.wait.active": "等待器仍活动，可能正在检查条件、投递事件，或等待事件被确认。",
      "help.wait.completed": "等待程序已成功退出；仍需 Agent 核对实际外部状态，不能据此认定业务成功。",
      "help.wait.failed": "等待程序、超时控制或事件投递失败，请查看详细结果。",
      "help.wait.cancelled": "已请求取消等待器，其受控等待进程将停止。",
      "help.loop.active": "循环未结束，正在执行当前轮次，或等待下一次计时器。",
      "help.loop.completed": "已达到次数或时限，后续不再安排轮次。",
      "help.loop.cancelled": "不再安排后续轮次；已经运行的工作不会被强行终止。",
      "event.init": "创建目标", "event.add_node": "添加节点", "event.edit_node": "修改节点", "event.start_node": "开始节点",
      "event.complete_node": "完成节点", "event.fail_node": "节点失败", "event.retry_node": "重试节点",
      "event.pause_goal": "暂停目标", "event.resume_goal": "恢复目标", "event.cancel_goal": "取消目标",
      "event.verify_goal": "目标验证", "event.finish_goal": "完成目标", "event.wake_node": "收到事件", "event.unknown": "变更",
      "event.actor.user": "你", "event.actor.event": "事件", "event.actor.agent": "Agent",
      "notice.wait": "已请求取消等待程序。", "notice.loop": "已取消后续轮次；已运行的任务不会被中断。",
      "notice.pause": "已暂停新任务派发；已运行任务可能继续。", "notice.cancel": "目标已取消；不会终止外部作业。",
      "notice.saved": "已保存，下次调度生效。",
      "confirm.wait": "取消这个等待？等待程序将停止。", "confirm.loop": "取消这个循环？后续轮次将停止。",
      "confirm.goal": "取消这个目标？未完成的任务将停止调度。",
      "empty.wait": "暂无普通等待", "empty.loop": "暂无循环", "state.active": "进行中",
      "common.add": "添加", "common.back": "返回", "common.details": "详情", "common.retry": "重试节点",
      "common.edit": "保存修改",
      "field.query": "查询 {value}", "field.notification": "通知 {value}", "field.deadline": "截止 {value}",
      "field.session": "会话 {value}", "field.triggered": "触发后 · {value}", "field.next": "下次 {value}",
      "field.iteration": "第 {value} 轮", "field.interval": "间隔 {value} 秒",
      "field.loopNext": "下轮 · {value}", "field.loopState": "{task} · 第 {iteration} 轮 · {status}",
      "field.noDependencies": "无", "field.acceptance": "验收条件", "field.waitLoop": "等待与循环",
      "field.externalCondition": "外部条件", "field.result": "执行结果", "field.waitDetails": "等待详情",
      "field.selectNode": "选择任务", "field.nodeKind.local": "主 Agent", "field.nodeKind.agent": "子 Agent",
      "field.nodeKind.external": "外部事件", "field.completeCount": "{done} / {total} 完成",
      "node.add": "添加节点", "node.id": "节点 ID", "node.title": "任务说明", "node.kind": "类型",
      "node.kind.local": "主 Agent 执行", "node.kind.agent": "子 Agent 执行", "node.kind.external": "外部事件",
      "node.dependencies": "依赖节点 ID（逗号分隔）", "node.acceptance": "验收条件（每行一条）",
      "node.write_paths": "写入路径（每行一条，留空表示只读）"
    }
  };
  let language = localStorage.getItem("wait-dashboard-language") || "en";

  function t(key, values = {}, fallback = key) {
    const text = copy[language][key] || fallback;
    return text.replace(/\{(\w+)\}/g, (placeholder, name) =>
      name in values ? String(values[name]) : placeholder);
  }

  function translate(node, key) {
    node.dataset.i18n = key;
    node.textContent = t(key);
  }

  function applyLanguage() {
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    $("languagePreference").value = language;
    document.querySelectorAll("[data-i18n]").forEach(node => {
      node.textContent = t(node.dataset.i18n);
    });
    for (const attribute of ["aria-label", "title", "placeholder"]) {
      document.querySelectorAll(`[data-i18n-${attribute}]`).forEach(node => {
        node.setAttribute(attribute, t(node.getAttribute(`data-i18n-${attribute}`)));
      });
    }
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("themePreference").value = theme;
  }

  function formatTime(value, options) {
    const date = timeDate(value);
    return date ? date.toLocaleString(language === "en" ? "en-US" : "zh-CN", options) : "—";
  }

  function formatDuration(milliseconds) {
    let seconds = Math.max(0, Math.floor(milliseconds / 1000));
    return [[86400, "day"], [3600, "hour"], [60, "minute"], [1, "second"]].flatMap(([size, unit]) => {
      const value = Math.floor(seconds / size);
      seconds %= size;
      return value || unit === "second" ? [`${value}${t(`duration.${unit}`)}`] : [];
    }).join(" ");
  }

  $("languagePreference").onchange = event => {
    language = event.target.value;
    localStorage.setItem("wait-dashboard-language", language);
    applyLanguage();
    onLanguageChange();
  };
  $("themePreference").onchange = event => {
    const theme = event.target.value;
    localStorage.setItem("wait-dashboard-theme", theme);
    applyTheme(theme);
  };

  applyTheme(localStorage.getItem("wait-dashboard-theme") || "light");
  applyLanguage();
  return { t, formatTime, formatDuration, translate };
}
