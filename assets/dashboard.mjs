import {activityId, activityWorkspace, relationNodes, watchState} from "./activity-model.mjs";
import {createGraphViewport} from "./graph-viewport.mjs";
import {createStatusHelp} from "./status-help.mjs";
import {createSessionPicker} from "./session-picker.mjs";
import {createAppearance} from "./appearance.mjs";
import {layoutGraph} from "./graph-layout.mjs";
import {durationRanges, nextSort, nodeRows, nodeState, nodeTimings, orderRows} from "./task-model.mjs";

const $ = id => document.getElementById(id);
const scope = new URLSearchParams(location.search);
const scopeQuery = "?" + scope.toString();
let tasks = {goals: [], watches: [], loops: [], linked_watches: []};
let mode = ["wait", "loop", "goal"].includes(scope.get("mode")) ? scope.get("mode") : "wait";
const focus = scope.get("focus") || "";
let focusPending = !!focus;

let selected = mode === "goal" ? focus : "";
let nodeId = mode === "goal" ? scope.get("node") || "" : focus ? activityId(mode, focus) : "";
let taskView = scope.get("view") === "graph" ? "graph" : "list";
let sort = null;

const { t, formatTime, formatDuration, translate } = createAppearance(() => render());
const help = createStatusHelp($("statusHelp"));
const viewport = createGraphViewport($("graph"), $("graphTools"), help.hide);
const sessionPicker = createSessionPicker(scope, t, () => ({mode, view: taskView}));

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function svgElement(tag, attributes) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

const colors = {
  pending: "var(--status-pending)", running: "var(--status-running)", dispatching: "var(--status-running)",
  ready: "var(--status-running)", blocked: "var(--status-pending)",
  waiting: "var(--status-waiting)", completed: "var(--status-completed)", failed: "var(--status-failed)",
  cancelled: "var(--status-failed)"
};

function currentWorkspace() {
  return mode === "goal" ? tasks.goals.find(goal => goal.path === selected) : activityWorkspace(tasks, mode);
}

function splitValues(value, separator) {
  return value.split(separator).map(item => item.trim()).filter(Boolean);
}

function nodeFields(values) {
  return {
    title: values.get("title"),
    depends_on: splitValues(values.get("depends_on"), ","),
    acceptance: splitValues(values.get("acceptance"), "\n")
  };
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}

async function act(action, fields = {}) {
  $("error").hidden = true;
  try {
    const response = await fetch("/api/action" + scopeQuery, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: selected, action, ...fields })
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    const notices = {
      wait: "notice.wait",
      loop: "notice.loop",
      pause: "notice.pause",
      cancel: "notice.cancel"
    };
    translate($("notice"), notices[fields.mode] || notices[action] || "notice.saved");
    $("notice").hidden = false;
    return true;
  } catch (error) {
    showError(error.message);
    return false;
  }
}

function renderEvents(events) {
  $("events").replaceChildren(...events.slice(-30).reverse().map(event => {
    const row = element("div", undefined, "event");
    const name = t(`event.${event.operation}`, {}, event.operation || t("event.unknown"));
    let actor = "agent";
    if (event.source === "user") actor = "user";
    else if (event.operation === "wake_node") actor = "event";
    const parts = [t(`event.actor.${actor}`), name];
    if (event.node_id) parts.push(event.node_id);
    row.append(
      element("time", formatTime(event.at)),
      element("span", parts.join(" · "))
    );
    return row;
  }));
}

async function cancelTask(task, kind) {
  if (!confirm(t(kind === "wait" ? "confirm.wait" : "confirm.loop"))) return;
  await act("cancel", kind === "wait"
    ? {mode: "wait", watch_id: task.watch_id}
    : {mode: "loop", path: task.path});
}

function renderSummary() {
  const validGoals = tasks.goals.filter(goal => !goal.error);
  const watches = [...tasks.watches, ...tasks.linked_watches];
  const running = validGoals.some(goal => Object.values(goal.nodes).some(node => ["running", "dispatching"].includes(node.status))) ||
    tasks.loops.some(loop => loop.status === "active" && loop.phase === "running") || watches.some(watch => watchState(watch) === "running");
  const ready = validGoals.some(goal => goal.status === "open" && goal.activity === "ready");
  const waiting = watches.filter(watch => watch.state === "active").length;
  const paused = validGoals.some(goal => goal.status === "paused");
  const verification = validGoals.some(goal => goal.status === "open" && goal.activity === "awaiting_verification");
  const failed = validGoals.some(goal => Object.values(goal.nodes).some(node => node.status === "failed")) ||
    watches.some(watch => watch.error || watch.state === "failed") || tasks.loops.some(loop => loop.error);
  const statuses = [
    [failed, "status.attention"],
    [paused, "status.paused"],
    [running, "status.running"],
    [waiting, "status.waiting"],
    [verification, "status.verification"],
    [ready, "status.ready"],
    [validGoals.length || watches.length || tasks.loops.length, "status.idle"],
    [true, "status.start"]
  ];
  const guidance = [
    [failed, "guide.failed"],
    [paused, "guide.paused"],
    [waiting, "guide.waiting", {count: waiting}],
    [verification, "guide.verification"],
    [running || ready, "guide.progress"],
    [true, "guide.idle"]
  ];
  translate($("sessionStatus"), statuses.find(([matches]) => matches)[1]);
  const [, key, values = {}] = guidance.find(([matches]) => matches);
  $("sessionNext").textContent = t(key, values);
}

function render() {
  help.hide();
  sessionPicker.render();
  renderSummary();
  const counts = {goal: tasks.goals.length, wait: tasks.watches.length + tasks.linked_watches.length, loop: tasks.loops.length};
  for (const button of $("modes").querySelectorAll("button")) {
    const kind = button.dataset.mode;
    button.textContent = `${t(`mode.${kind}`)} · ${counts[kind]}`;
    button.setAttribute("aria-selected", kind === mode);
  }
  if (mode === "goal") {
    if (!currentWorkspace()) selected = (tasks.goals.find(goal => goal.status === "open") || tasks.goals[0])?.path || "";
    $("goals").replaceChildren(...tasks.goals.map(goal => {
      const option = element("option", goal.objective?.trim().split(/[;；\n]/)[0] || goal.objective || goal.path);
      option.value = goal.path;
      return option;
    }));
    $("goals").value = selected;
  }

  const goal = currentWorkspace();
  $("empty").hidden = !!goal;
  $("content").hidden = !goal;
  $("goalToolbar").hidden = mode !== "goal" || !goal;
  $("events").parentElement.hidden = mode !== "goal";
  for (const action of ["pause", "resume", "cancel"]) $(action).disabled = !goal;
  if (!goal) return;
  if (goal.error) {
    showError(goal.error);
    $("content").hidden = true;
    return;
  }

  const objective = mode === "goal" ? goal.objective : t(`mode.${mode}`);
  $("objective").textContent = objective.trim().split(/[;；\n]/)[0] || objective;
  $("objective").title = objective;
  $("goalDescription").textContent = objective;
  $("goalDescription").parentElement.hidden = mode !== "goal";
  $("progress").parentElement.hidden = mode !== "goal";
  const available = {
    pause: goal.status === "open",
    resume: goal.status === "paused",
    cancel: ["open", "paused"].includes(goal.status)
  };
  for (const [action, enabled] of Object.entries(available)) {
    $(action).hidden = !enabled;
    $(action).disabled = !enabled;
  }
  $("add").hidden = mode !== "goal";
  $("add").disabled = goal.status !== "open";

  const nodes = Object.values(goal.nodes).filter(node => !node.context);
  const completed = nodes.filter(node => node.status === "completed").length;
  $("stats").replaceChildren(
    ...(mode === "goal" ? [statusBadge(goal.status, "goal")] : []),
    element("span", t("field.completeCount", {done: completed, total: nodes.length}))
  );
  $("progress").style.width = (nodes.length ? completed / nodes.length * 100 : 0) + "%";
  const timings = nodeTimings(goal);
  renderWorkspace(goal, timings);
  const details = $("details");
  const editor = details.querySelector("form");
  const node = goal.nodes[nodeId];
  const editable = mode === "goal" && node?.status === "pending" && goal.status === "open";
  const sameTask = editor?.dataset.node === nodeId && editor?.dataset.goal === goal.path;
  if (!editable || !sameTask || !editor.contains(document.activeElement)) {
    renderDetails(goal, editable && sameTask ? editor : null, timings);
  } else {
    details.querySelector("h3").textContent = node.title;
    details.querySelector(".badge").replaceWith(nodeBadge(goal, node));
    details.querySelector(".node-timeline").replaceWith(nodeTimeline(timings.get(nodeId)));
  }
  renderEvents(goal.events);
}

function statusMeaning(state, kind = mode === "goal" ? "node" : mode) {
  return t(`help.${kind}.${state}`, {}, t(`help.${state}`));
}

function statusBadge(state, kind = "node") {
  const badge = element("span", t(`state.${state}`), "badge");
  badge.dataset.state = state;
  help.attach(badge, statusMeaning(state, kind));
  badge.tabIndex = 0;
  return badge;
}

function nodeBadge(goal, node, state = nodeState(goal, node)) {
  return statusBadge(state, node.taskKind === "goal" ? "node" : node.taskKind || "node");
}

function taskContext(goal, node) {
  const state = nodeState(goal, node);
  if (node.taskKind) return node.summary || node.depends_on.map(id => goal.nodes[id].title).join(t("list.separator")) || t("workspace.noDependencies");
  if (state === "ready") return t("workspace.ready");
  if (state === "blocked") {
    return t("workspace.blocked", {count: node.depends_on.filter(id => goal.nodes[id].status !== "completed").length});
  }
  if (node.status === "pending" && goal.status === "paused") return t("workspace.paused");
  if (node.wait) return node.wait.event_note || node.wait.label || t("state.waiting");
  if (node.result) return typeof node.result === "string" ? node.result : node.result.summary || t(`state.${state}`);
  return node.depends_on.length ? node.depends_on.join(", ") : t("workspace.noDependencies");
}

function timeElement(date, options) {
  const time = element("time", formatTime(date, options));
  if (date) {
    time.dateTime = date.toISOString();
    time.title = formatTime(date, {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      second: "2-digit", hourCycle: "h23", timeZoneName: "short"
    });
  }
  return time;
}

function elapsedElement(range) {
  const span = element("span", "—", "elapsed");
  if (range) {
    span.dataset.elapsedStart = +range.start;
    if (range.end) span.dataset.elapsedEnd = +range.end;
    span.textContent = formatDuration((range.end || Date.now()) - range.start);
  }
  return span;
}

function phaseDuration(timing) {
  const {total, phase} = durationRanges(timing);
  if (["pending", "dispatching"].includes(timing.status) && !phase) return element("span", t("duration.pending"));
  const span = element("span", undefined, "phase-duration");
  span.title = t(phase ? `duration.${timing.status}` : "duration.total");
  span.append(elapsedElement(phase || total));
  return span;
}

function timeCell(timing) {
  const cell = element("span", undefined, "node-time");
  cell.append(element("span", t("duration.title"), "time-caption"), phaseDuration(timing));
  return cell;
}

function nodeTimeline(timing) {
  const timeline = element("dl", undefined, "node-timeline");
  const ranges = durationRanges(timing);
  for (const key of ["phase", "total"]) {
    if (!ranges[key]) continue;
    const value = element("dd");
    value.dataset.field = key;
    value.append(elapsedElement(ranges[key]));
    const label = key === "phase" ? t(`duration.${timing.status}`) : t("duration.total");
    timeline.append(element("dt", label), value);
  }
  for (const key of ["created", "started", "waiting", "finished", "updated"]) {
    if (!timing[key] && key !== "updated") continue;
    const value = element("dd");
    value.dataset.field = key;
    value.append(timeElement(timing[key], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
    }));
    timeline.append(element("dt", t(`time.${key}`)), value);
  }
  return timeline;
}

function showNode(id) {
  const hadFocus = document.activeElement?.dataset.node === id;
  const editor = id === nodeId ? $("details").querySelector("form") : null;
  nodeId = id;
  const goal = currentWorkspace();
  const timings = nodeTimings(goal);
  renderWorkspace(goal, timings);
  renderDetails(goal, editor, timings);
  if (hadFocus) {
    const root = $(taskView === "graph" ? "graph" : "nodeList");
    [...root.querySelectorAll("[data-node]")].find(node => node.dataset.node === id)?.focus({preventScroll: true});
  }
  if (matchMedia("(max-width: 760px)").matches) {
    const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    $("details").scrollIntoView({block: "start", behavior});
  }
}

function renderWorkspace(goal, timings = nodeTimings(goal)) {
  const firstMetric = $("nodeMetrics").querySelector("button");
  firstMetric.dataset.state = mode === "goal" ? "ready" : "failed";
  translate(firstMetric.querySelector(".metric-label span"), `state.${firstMetric.dataset.state}`);
  firstMetric.querySelector("i").className = "dot " + firstMetric.dataset.state;
  const graphTitle = mode === "goal" ? "graph.title" : "graph.relations";
  translate($("nodeViews").querySelector('[data-view="graph"]'), graphTitle);
  $("relationsHint").hidden = mode === "goal" || taskView !== "graph";
  const rows = nodeRows(goal);
  if (!Object.hasOwn(goal.nodes, nodeId)) nodeId = rows[0]?.id || "";
  const counts = {};
  for (const {state} of rows) counts[state] = (counts[state] || 0) + 1;
  const filter = $("nodeFilter").value;
  const search = $("nodeSearch").value.trim().toLowerCase();
  const matching = orderRows(rows.filter(({id, node, state}) =>
    (filter === "all" || state === filter) &&
    `${id} ${node.title}`.toLowerCase().includes(search)), timings, sort);
  $("filterControl").dataset.active = filter !== "all";
  for (const label of $("graphFooter").querySelectorAll(".legend > span")) {
    help.attach(label, statusMeaning(label.dataset.state));
    label.tabIndex = 0;
  }
  for (const button of $("nodeColumns").querySelectorAll("[data-sort]")) {
    const direction = sort?.key === button.dataset.sort ? sort.direction : null;
    button.setAttribute("aria-pressed", direction !== null);
    const next = direction === "desc" ? "asc" : direction === "asc" ? "default" : "desc";
    const column = t(button.dataset.sort === "duration" ? "duration.title" : "time.updated");
    button.setAttribute("aria-label", t(`sort.${next}`, {column}));
    button.querySelector("[data-sort-direction]").textContent = direction === "desc" ? "↓" : direction === "asc" ? "↑" : "↕";
  }
  const filterParent = taskView === "graph" ? $("graphFilter") : $("nodeColumns");
  if ($("statusColumn").parentElement !== filterParent) {
    if (taskView === "graph") filterParent.append($("statusColumn"));
    else filterParent.insertBefore($("statusColumn"), $("durationColumn"));
  }
  for (const option of $("nodeFilter").options) {
    const state = option.value;
    const label = t(state === "all" ? "workspace.all" : `state.${state}`);
    const count = counts[state] || 0;
    option.textContent = state === "all" ? label : `${label} · ${count}`;
    if (state !== "all") option.title = statusMeaning(state);
  }
  $("nodeCount").textContent = t("workspace.count", {shown: matching.length, total: rows.length});
  for (const button of $("nodeMetrics").querySelectorAll("button")) {
    button.querySelector("strong").textContent = counts[button.dataset.state] || 0;
    button.setAttribute("aria-pressed", filter === button.dataset.state);
    help.attach(button, statusMeaning(button.dataset.state));
  }
  for (const button of $("nodeViews").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", button.dataset.view === taskView);
  }
  $("nodeList").hidden = taskView !== "list";
  $("graph").hidden = taskView !== "graph";
  $("graphFooter").hidden = taskView !== "graph";
  $("graphTools").hidden = taskView !== "graph";
  if (taskView === "graph") {
    const selected = new Set(matching.map(({id}) => id));
    draw(mode === "goal" ? goal : {...goal, nodes: relationNodes(goal.nodes, selected)}, selected, timings);
    return;
  }
  if (!matching.length) {
    const empty = element("div", undefined, "empty");
    empty.append(
      element("h3", t(rows.length ? "workspace.empty" : mode === "goal" ? "workspace.emptyGoal" : `empty.${mode}`)),
      element("p", t(rows.length ? "workspace.emptyHint" : mode === "goal" ? "workspace.addFirst" : "activity.emptyHint"), "muted")
    );
    $("nodeRows").replaceChildren(empty);
    return;
  }
  $("nodeRows").replaceChildren(...matching.map(({id, node, state}) => {
    const button = element("button", undefined, "node-row");
    button.dataset.node = id;
    if (id === nodeId) button.id = "focusedTask";
    button.title = node.title;
    button.setAttribute("aria-pressed", id === nodeId);
    const name = element("span", undefined, "node-name");
    name.append(element("strong", node.title), element("code", node.shortId || id));
    const status = element("span");
    status.append(nodeBadge(goal, node, state));
    const updated = element("span", undefined, "node-updated");
    updated.append(timeElement(timings.get(id).updated, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
    }));
    button.append(name, element("span", taskContext(goal, node), "node-reason"), status, timeCell(timings.get(id)), updated);
    button.onclick = () => showNode(id);
    return button;
  }));
}

function draw(goal, matching, timings) {
  const {positions, edges, width, height} = layoutGraph(goal.nodes);
  const svg = svgElement("svg", {viewBox: `0 0 ${width} ${height}`, width, height, "aria-label": t("graph.title")});
  const definitions = svgElement("defs", {});
  const marker = svgElement("marker", {
    id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto"
  });
  marker.append(svgElement("path", {d: "M 0 0 L 10 5 L 0 10 z", fill: "var(--edge)"}));
  definitions.append(marker);
  svg.append(definitions);
  for (const {from, to, points} of edges) {
    const active = from === nodeId || to === nodeId;
    svg.append(svgElement("path", {
      d: points.map((point, index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" "),
      fill: "none", stroke: active ? "var(--primary)" : "var(--edge)", "stroke-width": active ? 2 : 1.4,
      "stroke-linejoin": "round", "marker-end": "url(#arrow)"
    }));
  }
  for (const [id, node] of Object.entries(goal.nodes)) {
    const position = positions[id];
    const selectedNode = id === nodeId;
    const group = svgElement("g", {
      transform: `translate(${position.x},${position.y})`, tabindex: 0, role: "button",
      "aria-label": node.title, "aria-pressed": selectedNode, "data-node": id,
      opacity: matching.has(id) || node.context ? 1 : .23
    });
    group.append(
      svgElement("rect", {width: 280, height: 114, rx: 10,
        fill: selectedNode ? "var(--node-selected)" : "var(--node)",
        stroke: selectedNode ? "var(--node-selected-border)" : "var(--node-border)", "stroke-width": selectedNode ? 2 : 1}),
      svgElement("rect", {x: 0, y: 14, width: 4, height: 86, rx: 2, fill: colors[nodeState(goal, node)]})
    );
    const content = svgElement("foreignObject", {x: 16, y: 11, width: 249, height: 98});
    const box = element("div", undefined, "graph-node");
    const meta = element("div", undefined, "graph-node-meta");
    meta.append(nodeBadge(goal, node), phaseDuration(timings.get(id)));
    box.append(element("strong", node.title), element("code", node.shortId || id), meta);
    content.append(box);
    const tooltip = svgElement("title", {});
    tooltip.textContent = node.title;
    group.append(content, tooltip);
    group.onclick = () => showNode(id);
    group.onkeydown = event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); showNode(id); }
    };
    svg.append(group);
  }
  viewport.render(svg);
}

function editForm(node) {
  const form = element("form");
  form.dataset.node = nodeId;
  form.dataset.goal = selected;
  const fields = [
    ["title", "node.title", node.title],
    ["depends_on", "node.dependencies", node.depends_on.join(", ")],
    ["acceptance", "node.acceptance", node.acceptance.join("\n")]
  ];
  for (const [name, labelKey, value] of fields) {
    const field = element("label");
    const label = element("span");
    translate(label, labelKey);
    const input = element(name === "acceptance" ? "textarea" : "input");
    input.name = name;
    input.value = value;
    input.required = name === "title";
    field.append(label, input);
    form.append(field);
  }
  const save = element("button", undefined, "primary");
  translate(save, "common.edit");
  save.style.marginTop = "16px";
  form.append(save);
  form.onsubmit = async event => {
    event.preventDefault();
    await act("edit", { id: nodeId, ...nodeFields(new FormData(form)) });
  };
  return form;
}

function openTask(nextMode, identity = "", nextNode = "") {
  if (mode !== nextMode) sort = null;
  mode = nextMode;
  if (mode === "goal" && identity) selected = identity;
  nodeId = mode === "goal" ? nextNode : identity ? activityId(mode, identity) : "";
  $("nodeSearch").value = "";
  $("nodeFilter").value = "all";
  viewport.reset();
  render();
}

function openOrigin(origin) {
  const available = origin.mode === "goal" ? tasks.goals.some(goal => goal.path === origin.path)
    : origin.mode === "loop" ? tasks.loops.some(loop => loop.path === origin.path)
    : [...tasks.watches, ...tasks.linked_watches].some(watch => watch.watch_id === origin.watch_id);
  if (!available) { showError(t("activity.parentUnavailable")); return; }
  openTask(origin.mode, origin.watch_id || origin.path, origin.node);
}

function renderActivityDetails(workspace, node, box) {
  if (node.context) {
    const open = element("button", t("activity.open", {kind: t(`mode.${node.taskKind}`)}));
    open.onclick = () => openOrigin(node.origin);
    box.append(open);
  }
  if (node.summary) box.append(element("h4", t("workspace.context")), element("p", node.summary, "task-note"));
  const related = [...node.depends_on, ...Object.keys(workspace.nodes).filter(id => workspace.nodes[id].depends_on.includes(nodeId))];
  if (related.length) {
    box.append(element("h4", t("activity.related")));
    const links = element("div", undefined, "node-dependencies");
    for (const id of related) {
      const button = element("button", workspace.nodes[id].title);
      button.onclick = () => showNode(id);
      links.append(button);
    }
    box.append(links);
  }
  const task = node.task;
  if (!task || task.error) return;
  if (task.owner) box.append(element("p", t("activity.managed"), "muted"));
  const meta = element("div", undefined, "task-meta");
  meta.append(element("span", t("field.deadline", {value: formatTime(task.deadline_at)})));
  if (node.taskKind === "wait") {
    meta.append(element("span", t("field.query", {value: task.query_status || "—"})),
      element("span", t("field.notification", {value: task.result?.notification || task.notification_channel?.status || "—"})));
  } else {
    meta.append(element("span", t("field.iteration", {value: task.iteration + (task.max_iterations ? " / " + task.max_iterations : "")})),
      element("span", t("field.interval", {value: task.interval_seconds})));
    if (task.next_run_at) meta.append(element("span", t("field.next", {value: formatTime(task.next_run_at)})));
  }
  box.append(meta);
  const cancellable = !node.context && (node.taskKind === "loop" ? task.status === "active" : task.state === "active" && !task.owner);
  if (cancellable) {
    const cancel = element("button", t("common.cancel"), "danger");
    cancel.onclick = () => cancelTask(task, node.taskKind);
    box.append(cancel);
  }
  const more = element("details");
  const detail = node.taskKind === "wait" ? {result: task.result, log_file: task.log_file}
    : {path: task.path, goal: task.goal, runs: task.runs, events: task.events};
  more.append(element("summary", t("common.details")), element("pre", JSON.stringify(detail, null, 2)));
  box.append(more);
}

function renderDetails(goal, editor = null, timings = nodeTimings(goal)) {
  const box = $("details");
  const editing = editor?.parentElement?.open || false;
  box.replaceChildren();
  const node = goal.nodes[nodeId];
  box.append(element("span", t("details.title"), "eyebrow"));
  if (!node) {
    box.append(element("p", t("field.selectNode"), "muted"));
    return;
  }
  const kind = node.taskKind ? t(`mode.${node.taskKind}`) : t(`field.nodeKind.${node.kind}`);
  box.append(
    nodeBadge(goal, node),
    element("h3", node.title),
    element("p", (node.shortId || nodeId) + " · " + kind, "muted")
  );
  box.append(nodeTimeline(timings.get(nodeId)));
  if (node.taskKind) { renderActivityDetails(goal, node, box); return; }
  const dependencies = element("div", undefined, "node-dependencies");
  if (node.depends_on.length) {
    for (const id of node.depends_on) {
      const button = element("button", id);
      button.title = goal.nodes[id].title;
      button.onclick = () => showNode(id);
      dependencies.append(button);
    }
  } else {
    dependencies.append(element("span", t("field.noDependencies"), "muted"));
  }
  const acceptance = element("ul");
  acceptance.append(...node.acceptance.map(item => element("li", item)));
  box.append(element("h4", t("details.dependencies")), dependencies);
  if (node.acceptance.length) box.append(element("b", t("field.acceptance")), acceptance);
  const relatedLoops = tasks.loops.filter(loop => loop.goal?.state === goal.path && loop.goal?.node === nodeId);
  const waits = tasks.linked_watches.filter(watch =>
    watch.owner.mode === "goal" && watch.owner.path === goal.path && watch.owner.node === nodeId);
  if (node.wait || relatedLoops.length || waits.length) {
    const context = element("section", undefined, "wait-context");
    context.append(element("b", t("field.waitLoop")));
    if (node.wait) {
      context.append(element("p", node.wait.label || t("field.externalCondition"), "task-note"));
      if (node.wait.event_note) context.append(element("p", t("field.triggered", {value: node.wait.event_note}), "task-note"));
    }
    for (const wait of waits) {
      context.append(element("p", `${t(`state.${wait.state}`)} · ${t("field.deadline", {value: formatTime(wait.deadline_at)})}`, "muted"));
    }
    for (const loop of relatedLoops) {
      context.append(element("p", t("field.loopState", {task: loop.task, iteration: loop.iteration, status: t(`state.${loop.status}`)}), "task-note"));
      if (loop.event_note) context.append(element("p", t("field.loopNext", {value: loop.event_note}), "task-note"));
    }
    box.append(context);
  }
  if (node.result) {
    box.append(element("h4", t("field.result")),
      element("p", typeof node.result === "string" ? node.result : node.result.summary || JSON.stringify(node.result), "task-note"));
  }
  if (node.wait) {
    const metadata = element("details", undefined, "muted");
    metadata.append(element("summary", t("field.waitDetails")), element("pre", JSON.stringify(node.wait, null, 2)));
    box.append(metadata);
  }
  if (node.status === "failed" && goal.status === "open") {
    const retry = element("button", t("common.retry"));
    retry.onclick = () => act("retry", { id: nodeId });
    box.append(retry);
  }
  if (node.status === "pending" && goal.status === "open") {
    const edit = element("details", undefined, "edit-node");
    edit.open = editing;
    edit.append(element("summary", t("details.edit")), editor || editForm(node));
    box.append(edit);
  }
}

$("goals").onchange = event => openTask("goal", event.target.value);
$("pause").onclick = () => act("pause");
$("resume").onclick = () => act("resume");
$("cancel").onclick = () => {
  if (confirm(t("confirm.goal"))) act("cancel");
};
$("add").onclick = () => $("addDialog").showModal();
$("dismiss").onclick = () => $("addDialog").close();
$("addForm").onsubmit = async event => {
  event.preventDefault();
  const form = event.target;
  const values = new FormData(form);
  const writes = splitValues(values.get("write_paths"), "\n");
  const added = await act("add", {
    id: values.get("id"), kind: values.get("kind"), ...nodeFields(values),
    read_only: !writes.length, write_paths: writes
  });
  if (added) {
    $("addDialog").close();
    form.reset();
  }
};

$("modes").onclick = event => {
  const button = event.target.closest("button[data-mode]");
  if (button && mode !== button.dataset.mode) openTask(button.dataset.mode);
};

function applyFilter() {
  if (mode !== "goal" && taskView === "graph") viewport.reset();
  renderWorkspace(currentWorkspace());
}
$("nodeSearch").oninput = applyFilter;
$("nodeMetrics").onclick = event => {
  const button = event.target.closest("button[data-state]");
  if (!button) return;
  const filter = $("nodeFilter");
  filter.value = filter.value === button.dataset.state ? "all" : button.dataset.state;
  applyFilter();
};
$("nodeFilter").onchange = applyFilter;
$("nodeColumns").onclick = event => {
  const button = event.target.closest("[data-sort]");
  if (!button) return;
  sort = nextSort(sort, button.dataset.sort);
  renderWorkspace(currentWorkspace());
};
$("nodeViews").onclick = event => {
  const button = event.target.closest("button[data-view]");
  if (button) { taskView = button.dataset.view; renderWorkspace(currentWorkspace()); }
};
function refreshDurations() {
  if (document.hidden) return;
  const now = Date.now();
  document.querySelectorAll("[data-elapsed-start]:not([data-elapsed-end])").forEach(node => {
    node.textContent = formatDuration(now - Number(node.dataset.elapsedStart));
  });
  if (sort?.key === "duration" && taskView === "list" && !$("content").hidden) {
    const goal = currentWorkspace();
    if (!goal || goal.error) return;
    const root = $("nodeRows");
    const visible = new Map([...root.querySelectorAll(".node-row")].map(row => [row.dataset.node, row]));
    const ordered = orderRows(nodeRows(goal).filter(row => visible.has(row.id)), nodeTimings(goal), sort, now);
    const focused = document.activeElement;
    ordered.forEach(({id}, index) => {
      const row = visible.get(id);
      if (root.children[index] !== row) root.insertBefore(row, root.children[index]);
    });
    if (focused?.classList.contains("node-row") && document.activeElement !== focused) focused.focus({preventScroll: true});
  }
}
setInterval(refreshDurations, 1000);
document.addEventListener("visibilitychange", refreshDurations);

const stream = new EventSource("/events" + scopeQuery);
stream.onmessage = event => {
  tasks = JSON.parse(event.data);
  translate($("connection"), "connection.live");
  render();
  if (focusPending) {
    document.getElementById("focusedTask")?.scrollIntoView({block: "center", behavior: "smooth"});
    focusPending = false;
  }
};
stream.onerror = () => translate($("connection"), "connection.reconnecting");
