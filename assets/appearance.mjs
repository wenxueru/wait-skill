import {timeDate} from "./task-model.mjs";

const $ = id => document.getElementById(id);

export function createAppearance(onLanguageChange) {
  const copy = {
    en: {
      "goal.description": "Full objective", "workspace.display": "Task display", "workspace.list": "Task list",
      "workspace.search": "Search tasks or IDs…", "workspace.filter": "Filter tasks", "workspace.all": "All tasks",
      "workspace.count": "{shown} / {total} tasks", "workspace.empty": "No matching tasks",
      "workspace.emptyHint": "Try another search or status filter.", "workspace.task": "Task", "workspace.state": "Status",
      "workspace.emptyGoal": "No tasks yet", "workspace.addFirst": "Add your first task to start building this goal.",
      "workspace.context": "Dependency / next step", "workspace.ready": "Dependencies complete",
      "workspace.blocked": "Waiting on {count} dependencies", "workspace.paused": "Goal paused",
      "workspace.noDependencies": "No dependencies", "state.ready": "Ready", "state.blocked": "Blocked",
      "details.title": "Task details", "details.dependencies": "Dependencies", "details.edit": "Edit task",
      "graph.fit": "Fit", "graph.zoomIn": "Zoom in", "graph.zoomOut": "Zoom out",
      "page.title": "Wait dashboard", "list.separator": ", ",
      "scope.current": "Current session", "scope.all": "All sessions", "scope.openHint": "Open with waitctl dashboard --session",
      "settings.title": "Settings", "language.label": "Language", "theme.label": "Appearance",
      "time.updated": "Updated", "time.created": "Created", "time.started": "Started", "time.waiting": "Waiting since", "time.finished": "Ended",
      "theme.light": "Light", "theme.dark": "Dark",
      "connection.connecting": "Connecting", "connection.live": "● Live", "connection.reconnecting": "Reconnecting",
      "session.tasks": "Session tasks", "session.connecting": "Connecting to the session…",
      "status.attention": "Tasks need attention", "status.paused": "A goal is paused", "status.running": "Work in progress",
      "status.waiting": "Waiting for an event", "status.verification": "Final verification pending", "status.ready": "Ready to continue",
      "status.idle": "No active tasks", "status.start": "Ready when you are",
      "guide.paused": "Paused goals will not dispatch new work; running tasks may continue.",
      "guide.waiting": "{count} program-managed wait(s) will notify their session when an event arrives.",
      "guide.failed": "Review the task result and evidence, then retry or adjust the plan.",
      "guide.verification": "Nodes are complete; the agent still needs to verify the original goal and record evidence.",
      "guide.progress": "The agent follows dependencies and acceptance criteria; the graph updates as work changes.",
      "guide.idle": "Create a goal, wait, or loop in the conversation to see it here.",
      "mode.session": "Session", "mode.goal": "Goals", "mode.wait": "Waits", "mode.loop": "Loops", "view.aria": "Task views",
      "select.goal": "Select a goal", "goal.pause": "Pause", "goal.resume": "Resume", "common.cancel": "Cancel",
      "empty.goals.title": "No goals yet", "empty.goals.body": "This session has no DAG. Create one with waitctl goal init.",
      "graph.title": "Dependency map", "graph.add": "+ Node", "events.recent": "Recent changes",
      "state.open": "Active", "state.paused": "Paused", "state.completed": "Completed", "state.cancelled": "Cancelled",
      "state.pending": "Pending", "state.dispatching": "Dispatching", "state.running": "Running", "state.waiting": "Waiting", "state.failed": "Failed",
      "event.init": "Goal created", "event.add_node": "Node added", "event.edit_node": "Node updated", "event.start_node": "Node started",
      "event.complete_node": "Node completed", "event.fail_node": "Node failed", "event.retry_node": "Node retried",
      "event.pause_goal": "Goal paused", "event.resume_goal": "Goal resumed", "event.cancel_goal": "Goal cancelled",
      "event.verify_goal": "Goal verified", "event.finish_goal": "Goal completed", "event.wake_node": "Event received", "event.unknown": "Change",
      "event.actor.user": "You", "event.actor.event": "Event", "event.actor.agent": "Agent",
      "notice.wait": "Cancellation requested. The waiting process will stop.",
      "notice.loop": "The loop is cancelled; a task already running will continue.",
      "notice.pause": "New work is paused; running tasks may continue.",
      "notice.cancel": "The goal is cancelled; external jobs will not be stopped.",
      "notice.saved": "Saved to task state. Scheduling will follow the change; the page will not wake the agent.",
      "confirm.wait": "Cancel this wait? The waiting process will stop.", "confirm.loop": "Cancel this loop? Future iterations will stop.",
      "confirm.goal": "Cancel this goal? Incomplete tasks will stop being scheduled.",
      "empty.wait": "No waits", "empty.loop": "No loops", "state.active": "Active",
      "common.add": "Add", "common.back": "Back", "common.details": "Details", "common.retry": "Retry node",
      "common.edit": "Save changes",
      "field.query": "Query {value}", "field.notification": "Notification {value}", "field.deadline": "Due {value}",
      "field.session": "Session {value}", "field.triggered": "On trigger · {value}", "field.next": "Next {value}",
      "field.iteration": "Iteration {value}", "field.interval": "Every {value}s",
      "field.loopNext": "Next · {value}", "field.loopState": "{task} · iteration {iteration} · {status}",
      "field.dependencies": "Dependencies: {value}",
      "field.noDependencies": "None", "field.acceptance": "Acceptance criteria", "field.waitLoop": "Waits and loops",
      "field.externalCondition": "External condition", "field.result": "Result", "field.waitDetails": "Wait details",
      "field.selectNode": "Select a node to view its details", "field.nodeKind.local": "Main agent", "field.nodeKind.agent": "Subagent",
      "field.nodeKind.external": "External event", "field.completeCount": "{done} of {total} completed",
      "history.goals": "Goal history · {count}", "history.waits": "Wait history · {count}", "history.loops": "Loop history · {count}",
      "section.independentWaits": "Independent waits", "section.independentLoops": "Independent loops",
      "node.add": "Add node", "node.id": "Node ID", "node.title": "Task description", "node.kind": "Type",
      "node.kind.local": "Main agent", "node.kind.agent": "Subagent", "node.kind.external": "External event",
      "node.dependencies": "Dependencies (comma-separated IDs)", "node.acceptance": "Acceptance criteria (one per line)",
      "node.write_paths": "Write paths (one per line; leave blank for read-only)"
    },
    zh: {
      "goal.description": "查看完整目标", "workspace.display": "任务展示方式", "workspace.list": "任务列表",
      "workspace.search": "搜索任务或 ID…", "workspace.filter": "筛选任务", "workspace.all": "全部任务",
      "workspace.count": "{shown} / {total} 个任务", "workspace.empty": "没有匹配的任务",
      "workspace.emptyHint": "试试其他关键词或状态。", "workspace.task": "任务", "workspace.state": "状态",
      "workspace.emptyGoal": "还没有任务", "workspace.addFirst": "添加第一个任务，开始构建这个目标。",
      "workspace.context": "依赖 / 下一步", "workspace.ready": "依赖已完成，可以推进",
      "workspace.blocked": "等待 {count} 个依赖完成", "workspace.paused": "目标已暂停",
      "workspace.noDependencies": "无依赖", "state.ready": "可推进", "state.blocked": "依赖未就绪",
      "details.title": "任务详情", "details.dependencies": "依赖任务", "details.edit": "编辑任务",
      "graph.fit": "适应窗口", "graph.zoomIn": "放大", "graph.zoomOut": "缩小",
      "page.title": "Wait · 看板", "list.separator": "、",
      "scope.current": "当前会话", "scope.all": "全部会话", "scope.openHint": "通过 waitctl dashboard --session 打开",
      "settings.title": "设置", "language.label": "语言", "theme.label": "外观",
      "time.updated": "最近更新", "time.created": "创建时间", "time.started": "开始时间", "time.waiting": "开始等待", "time.finished": "结束时间",
      "theme.light": "浅色", "theme.dark": "深色",
      "connection.connecting": "连接中", "connection.live": "● 实时", "connection.reconnecting": "重连中",
      "session.tasks": "会话任务", "session.connecting": "正在连接会话…",
      "status.attention": "有任务需要处理", "status.paused": "有目标已暂停", "status.running": "工作推进中",
      "status.waiting": "等待外部事件", "status.verification": "等待最终验收", "status.ready": "有任务可以推进",
      "status.idle": "当前没有待运行任务", "status.start": "准备开始",
      "guide.paused": "已暂停的目标不会派发新任务；已有任务可能继续运行。",
      "guide.waiting": "{count} 个等待由程序运行，事件到达后会通知所属会话。",
      "guide.failed": "打开任务检查结果与证据，再决定重试或调整计划。",
      "guide.verification": "节点已完成，Agent 仍需核对原始目标、记录证据并完成验收。",
      "guide.progress": "Agent 按依赖和验收条件推进；任务图随工作变化实时更新。",
      "guide.idle": "在对话中创建目标、普通等待或循环后，会自动出现在这里。",
      "mode.session": "当前任务", "mode.goal": "目标", "mode.wait": "等待", "mode.loop": "循环", "view.aria": "任务视图",
      "select.goal": "选择目标", "goal.pause": "暂停", "goal.resume": "恢复", "common.cancel": "取消",
      "empty.goals.title": "还没有目标", "empty.goals.body": "本会话尚无 DAG。通过 waitctl goal init 创建。",
      "graph.title": "依赖图", "graph.add": "+ 节点", "events.recent": "最近变更",
      "state.open": "进行中", "state.paused": "已暂停", "state.completed": "已完成", "state.cancelled": "已取消",
      "state.pending": "待处理", "state.dispatching": "分派中", "state.running": "执行中", "state.waiting": "等待事件", "state.failed": "失败",
      "event.init": "创建目标", "event.add_node": "添加节点", "event.edit_node": "修改节点", "event.start_node": "开始节点",
      "event.complete_node": "完成节点", "event.fail_node": "节点失败", "event.retry_node": "重试节点",
      "event.pause_goal": "暂停目标", "event.resume_goal": "恢复目标", "event.cancel_goal": "取消目标",
      "event.verify_goal": "目标验证", "event.finish_goal": "完成目标", "event.wake_node": "收到事件", "event.unknown": "变更",
      "event.actor.user": "你", "event.actor.event": "事件", "event.actor.agent": "Agent",
      "notice.wait": "已请求取消等待程序。", "notice.loop": "已取消后续轮次；已运行的任务不会被中断。",
      "notice.pause": "已暂停新任务派发；已运行任务可能继续。", "notice.cancel": "目标已取消；不会终止外部作业。",
      "notice.saved": "已保存到任务状态，后续调度按此执行。页面不会立即唤醒 Agent。",
      "confirm.wait": "取消这个等待？等待程序将停止。", "confirm.loop": "取消这个循环？后续轮次将停止。",
      "confirm.goal": "取消这个目标？未完成的任务将停止调度。",
      "empty.wait": "暂无普通等待", "empty.loop": "暂无循环", "state.active": "进行中",
      "common.add": "添加", "common.back": "返回", "common.details": "详情", "common.retry": "重试节点",
      "common.edit": "保存修改",
      "field.query": "查询 {value}", "field.notification": "通知 {value}", "field.deadline": "截止 {value}",
      "field.session": "会话 {value}", "field.triggered": "触发后 · {value}", "field.next": "下次 {value}",
      "field.iteration": "第 {value} 轮", "field.interval": "间隔 {value} 秒",
      "field.loopNext": "下轮 · {value}", "field.loopState": "{task} · 第 {iteration} 轮 · {status}",
      "field.dependencies": "依赖：{value}",
      "field.noDependencies": "无", "field.acceptance": "验收条件", "field.waitLoop": "等待与循环",
      "field.externalCondition": "外部条件", "field.result": "执行结果", "field.waitDetails": "等待详情",
      "field.selectNode": "选择节点查看详情", "field.nodeKind.local": "主 Agent", "field.nodeKind.agent": "子 Agent",
      "field.nodeKind.external": "外部事件", "field.completeCount": "{done} / {total} 完成",
      "history.goals": "目标历史 · {count}", "history.waits": "等待历史 · {count}", "history.loops": "循环历史 · {count}",
      "section.independentWaits": "独立等待", "section.independentLoops": "独立循环",
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
  return { t, formatTime, translate };
}
