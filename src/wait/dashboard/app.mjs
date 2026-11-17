import {guard, html, keyed, nothing, render as renderTemplate, repeat} from "../../../assets/vendor/ui.mjs";
import {activityId, activityWorkspace, collapseLoopTimers, relationNodes, watchState} from "./models/activity.mjs";
import {createGraphViewport} from "./controls/viewport.mjs";
import {findTask, taskUrl} from "./models/links.mjs";
import {createStatusFilter} from "./controls/status-filter.mjs";
import {createStatusHelp} from "./controls/status-help.mjs";
import {createSessionPicker} from "./controls/session-picker.mjs";
import {createAppearance} from "./appearance.mjs";
import {layoutGraph} from "./graph/layout.mjs";
import {durationRanges, matchesTaskScope, scopeForStatuses, nextSort, nodeRows, nodeState, nodeTimings, orderRows} from "./models/tasks.mjs";

const $ = id => document.getElementById(id);
const scope = new URLSearchParams(location.search);
const scopeQuery = "?" + scope.toString();
$("taskScope").value = ["history", "all"].includes(scope.get("tasks")) ? scope.get("tasks") : "current";
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
const statusFilter = createStatusFilter($("filterControl"), $("nodeFilter"), t, filterStatuses);
const viewport = createGraphViewport($("graph"), $("graphTools"), help.hide);
const sessionPicker = createSessionPicker(scope, t, () => ({mode, view: taskView, tasks: $("taskScope").value}));

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
  translate($("sessionStatus"), statuses.find(([matches]) => matches)[1]);
}

function render(reveal = false) {
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
  $("objective").parentElement.hidden = mode !== "goal";
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
  renderTemplate(html`${mode === "goal" ? statusBadge(goal.status, "goal") : nothing}
    <span>${t("field.completeCount", {done: completed, total: nodes.length})}</span>`, $("stats"));
  $("progress").style.width = (nodes.length ? completed / nodes.length * 100 : 0) + "%";
  const focusedNode = goal.nodes[nodeId];
  if (reveal && focusedNode && !matchesTaskScope(nodeState(goal, focusedNode), $("taskScope").value)) {
    $("taskScope").value = matchesTaskScope(nodeState(goal, focusedNode), "history") ? "history" : "current";
    statusFilter.clear();
    $("nodeSearch").value = focusedNode.shortId || nodeId;
  }
  const timings = nodeTimings(goal);
  renderWorkspace(goal, timings);
  renderDetails(goal, timings);
  renderEvents(goal.events);
}

function statusMeaning(state, kind = mode === "goal" ? "node" : mode) {
  return t(`help.${kind}.${state}`, {}, t(`help.${state}`));
}

function statusBadge(state, kind = "node") {
  const meaning = statusMeaning(state, kind);
  return html`<span class="badge" data-state=${state} data-status-help=${meaning}
    aria-description=${meaning} tabindex="0">${t(`state.${state}`)}</span>`;
}

function nodeBadge(goal, node, state = nodeState(goal, node)) {
  return statusBadge(state, node.taskKind === "goal" ? "node" : node.taskKind || "node");
}

function dependencyCell(workspace, node) {
  return html`<span class="node-deps" data-empty=${node.depends_on.length === 0}>
    <small class="dependency-caption">${t(mode === "goal" ? "workspace.dependencies" : "workspace.parent")}</small>
    ${node.depends_on.length ? node.depends_on.map(id => {
      const parent = workspace.nodes[id];
      const origin = parent.origin || {mode: "goal", path: workspace.path, node: id};
      const task = findTask(tasks, origin);
      if (!task) return html`<span title=${t("activity.parentUnavailable")}>${parent.title}</span>`;
      return html`<a href=${taskUrl(scope, origin, task)} title=${parent.title} @click=${event => {
        event.stopPropagation();
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        taskView = "list";
        openOrigin(origin);
      }}>${parent.title}</a>`;
    }) : html`<span class="muted">${t("field.noDependencies")}</span>`}
  </span>`;
}

const shortTime = {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"};
const fullTime = {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hourCycle: "h23", timeZoneName: "short"
};

function timeElement(date) {
  return html`<time datetime=${date?.toISOString() ?? nothing} title=${date ? formatTime(date, fullTime) : nothing}>
    ${formatTime(date, shortTime)}</time>`;
}

function elapsedElement(range) {
  return guard([range ? +range.start : null, range?.end ? +range.end : null, document.documentElement.lang], () => {
    const span = element("span", "—", "elapsed");
    if (range) {
      span.dataset.elapsedStart = +range.start;
      if (range.end) span.dataset.elapsedEnd = +range.end;
      span.textContent = formatDuration((range.end || Date.now()) - range.start);
    }
    return span;
  });
}

function phaseDuration(timing) {
  const {total, phase} = durationRanges(timing);
  if (["pending", "dispatching"].includes(timing.status) && !phase) return html`<span>${t("duration.pending")}</span>`;
  return html`<span class="phase-duration" title=${t(phase ? `duration.${timing.status}` : "duration.total")}>
    ${elapsedElement(phase || total)}</span>`;
}

function nodeTimeline(timing) {
  const ranges = durationRanges(timing);
  return html`<dl class="node-timeline">
    ${["phase", "total"].map(key => ranges[key] ? html`
      <dt>${t(key === "phase" ? `duration.${timing.status}` : "duration.total")}</dt>
      <dd data-field=${key}>${elapsedElement(ranges[key])}</dd>` : nothing)}
    ${["created", "started", "waiting", "finished", "updated"].map(key => timing[key] || key === "updated" ? html`
      <dt>${t(`time.${key}`)}</dt><dd data-field=${key}>${timeElement(timing[key])}</dd>` : nothing)}
  </dl>`;
}

function showNode(id) {
  const hadFocus = document.activeElement?.closest("[data-node]")?.dataset.node === id;
  nodeId = id;
  const goal = currentWorkspace();
  if (taskView === "list" && !goal.nodes[id].context && !matchesTaskScope(nodeState(goal, goal.nodes[id]), $("taskScope").value)) render(true);
  else {
    const timings = nodeTimings(goal);
    renderWorkspace(goal, timings);
    renderDetails(goal, timings);
  }
  if (hadFocus) {
    const root = $(taskView === "graph" ? "graph" : "nodeList");
    const target = [...root.querySelectorAll("[data-node]")].find(node => node.dataset.node === id);
    (target?.querySelector(".node-name") || target)?.focus({preventScroll: true});
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
  help.attach($("graphHelp"), t("graph.help") + (mode === "goal" ? "" : " " + t("activity.relationsHint")));
  translate($("nodeColumns").querySelector(".dependency-column"), mode === "goal" ? "workspace.dependencies" : "workspace.parent");
  const rows = nodeRows(goal);
  const taskScope = $("taskScope").value;
  const scopedRows = rows.filter(({state}) => matchesTaskScope(state, taskScope));
  const counts = {};
  for (const {state} of rows) counts[state] = (counts[state] || 0) + 1;
  const filter = statusFilter.selected;
  const search = $("nodeSearch").value.trim().toLowerCase();
  const matching = orderRows(scopedRows.filter(({id, node, state}) =>
    (!filter.length || filter.includes(state)) &&
    `${id} ${node.title}`.toLowerCase().includes(search)), timings, sort);
  const matchingIds = new Set(matching.map(({id}) => id));
  let graphNodes;
  if (taskView === "graph" && matching.length) {
    graphNodes = goal.nodes;
    if (mode !== "goal") graphNodes = relationNodes(goal.nodes, matchingIds);
    else if (taskScope !== "all") graphNodes = relationNodes(goal.nodes, new Set(scopedRows.map(({id}) => id)));
    const grouped = collapseLoopTimers(graphNodes);
    graphNodes = grouped.nodes;
    nodeId = grouped.members.get(nodeId) || nodeId;
    for (const [timer, owner] of grouped.members) {
      if (matchingIds.has(timer)) matchingIds.add(owner);
    }
  }
  const selectionVisible = matching.length && (taskView === "graph"
    ? Object.hasOwn(graphNodes, nodeId) : matchingIds.has(nodeId) || goal.nodes[nodeId]?.context);
  if (!selectionVisible) nodeId = matching[0]?.id || "";
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
  statusFilter.render(counts, statusMeaning);
  $("nodeCount").textContent = t("workspace.count", {shown: matching.length, total: rows.length});
  for (const option of $("taskScope").options) {
    const count = rows.filter(({state}) => matchesTaskScope(state, option.value)).length;
    option.textContent = `${t(option.value === "all" ? "workspace.all" : `workspace.${option.value}`)} · ${count}`;
  }
  for (const button of $("nodeMetrics").querySelectorAll("button")) {
    button.querySelector("strong").textContent = counts[button.dataset.state] || 0;
    button.setAttribute("aria-pressed", filter.includes(button.dataset.state));
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
    draw({...goal, nodes: matching.length ? graphNodes : {}}, matchingIds, timings);
    if (!matching.length) $("graph").append(emptyTasks(rows, taskScope));
    return;
  }
  if (!matching.length) {
    renderTemplate(emptyTasks(rows, taskScope), $("nodeRows"));
    return;
  }
  renderRows(goal, matching, timings);
}

function renderRows(goal, rows, timings) {
  renderTemplate(repeat(rows, row => row.id, ({id, node, state}) => {
    const timing = timings.get(id);
    const next = node.task?.event_note || node.wait?.event_note || "";
    const focused = id === nodeId;
    return html`<div class="node-row" tabindex="-1" role="group" aria-label=${node.title}
      data-node=${id} id=${focused ? "focusedTask" : nothing} title=${node.title}
      aria-current=${focused} @click=${() => showNode(id)}>
      <button class="node-name" type="button" aria-pressed=${focused}>
        <strong>${node.title}</strong><code>${node.shortId || id}</code>
      </button>
      ${dependencyCell(goal, node)}
      <span class="node-next" data-empty=${!next} title=${next}>
        <small class="next-caption">${t("workspace.next")}</small><span class="node-reason">${next || "—"}</span>
      </span>
      <span class="node-status">${nodeBadge(goal, node, state)}</span>
      <span class="node-time"><span class="time-caption">${t("duration.title")}</span>${phaseDuration(timing)}</span>
      <span class="node-updated">${timeElement(timing.updated)}</span>
    </div>`;
  }), $("nodeRows"));
}

function emptyTasks(rows, taskScope) {
  const empty = element("div", undefined, "empty");
  const history = rows.filter(({state}) => matchesTaskScope(state, "history")).length;
  const finished = taskScope === "current" && rows.length > 0 && history === rows.length;
  const title = finished ? "workspace.noCurrent" : rows.length ? "workspace.empty" : mode === "goal" ? "workspace.emptyGoal" : `empty.${mode}`;
  const hint = finished ? "workspace.historyHint" : rows.length ? "workspace.emptyHint" : mode === "goal" ? "workspace.addFirst" : "activity.emptyHint";
  empty.append(element("h3", t(title)), element("p", t(hint), "muted"));
  if (taskScope === "current" && history) {
    const button = element("button", t("workspace.showHistory"));
    button.onclick = () => {
      $("taskScope").value = "history";
      statusFilter.clear();
      $("nodeSearch").value = "";
      applyFilter();
    };
    empty.append(button);
  }
  return empty;
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
      "aria-label": node.title, "aria-pressed": selectedNode, "data-node": id, "data-kind": node.taskKind || "goal",
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
    renderTemplate(html`${nodeBadge(goal, node)}${phaseDuration(timings.get(id))}`, meta);
    if (node.taskKind === "loop") {
      box.classList.add("loop-node");
      const loop = node.task?.error ? null : node.task;
      const header = element("div", undefined, "loop-kicker");
      header.append(element("span", "↻ " + t("mode.loop")), element("span", loop ? t("field.iteration", {value: loop.iteration + (loop.max_iterations ? " / " + loop.max_iterations : "")}) : ""));
      const schedule = loop ? t("loop.every", {duration: formatDuration(loop.interval_seconds * 1000)}) +
        (loop.next_run_at ? " · " + t("field.next", {value: formatTime(loop.next_run_at, {hour: "2-digit", minute: "2-digit"})}) : "") : node.shortId;
      const summary = loop?.runs?.at(-1)?.summary;
      box.append(header, element("strong", node.title), element("small", schedule, "loop-schedule"));
      if (summary) {
        const result = element("small", summary, "loop-result");
        result.title = summary;
        box.append(result);
      }
      if (node.timerCount) {
        const failed = node.failedTimers;
        const label = (failed ? t("loop.failedTimers", {count: failed}) + " · " : "") + t("loop.timers", {count: node.timerCount});
        meta.append(element("small", label, failed ? "loop-alert" : ""));
      }
      box.append(meta);
    } else box.append(element("strong", node.title), element("code", node.shortId || id), meta);
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

function editForm(node, id) {
  return html`<details class="edit-node">
    <summary>${t("details.edit")}</summary>
    <form data-node=${id} @submit=${event => {
      event.preventDefault();
      act("edit", {id, ...nodeFields(new FormData(event.currentTarget))});
    }}>
      <label><span>${t("node.title")}</span><input name="title" required .value=${guard([], () => node.title)}></label>
      <label><span>${t("node.dependencies")}</span>
        <input name="depends_on" .value=${guard([], () => node.depends_on.join(", "))}></label>
      <label><span>${t("node.acceptance")}</span>
        <textarea name="acceptance" .value=${guard([], () => node.acceptance.join("\n"))}></textarea></label>
      <button class="primary" style="margin-top: 16px">${t("common.edit")}</button>
    </form>
  </details>`;
}

function openTask(nextMode, identity = "", nextNode = "") {
  if (mode !== nextMode) sort = null;
  mode = nextMode;
  if (mode === "goal" && identity) selected = identity;
  nodeId = mode === "goal" ? nextNode : identity ? activityId(mode, identity) : "";
  $("nodeSearch").value = "";
  statusFilter.clear();
  $("taskScope").value = "current";
  viewport.reset();
  render(true);
}

function openOrigin(origin) {
  if (!findTask(tasks, origin)) { showError(t("activity.parentUnavailable")); return; }
  openTask(origin.mode, origin.watch_id || origin.path, origin.node);
}

function loopHistory(node) {
  const runs = node.task?.runs?.slice(-5).reverse() ?? [];
  const timers = [...tasks.watches, ...tasks.linked_watches].filter(watch =>
    watch.owner?.mode === "loop" && watch.owner.path === node.origin.path);
  timers.sort((a, b) => b.submitted_at - a.submitted_at);
  return html`
    ${runs.length ? html`<h4>${t("loop.recent")}</h4><ol class="loop-history">
      ${repeat(runs, run => run.iteration, run => html`<li>
        <b>${t("field.iteration", {value: run.iteration})}</b><time>${formatTime(run.completed_at)}</time><p>${run.summary}</p>
      </li>`)}
    </ol>` : nothing}
    ${timers.length ? html`<details class="loop-timers">
      <summary>${t("loop.timerHistory", {count: timers.length})}</summary><ol class="loop-history">
        ${repeat(timers, timer => timer.watch_id, timer => html`<li>
          ${statusBadge(watchState(timer), "wait")}
          <button @click=${() => { taskView = "list"; openOrigin({mode: "wait", watch_id: timer.watch_id}); }}>
            ${timer.watch_id.slice(0, 8)}</button>
          <time>${formatTime(timer.submitted_at)}</time>
        </li>`)}
      </ol>
    </details>` : nothing}
  `;
}

function activityDetails(workspace, node, id) {
  const related = [...node.depends_on, ...Object.keys(workspace.nodes).filter(other =>
    workspace.nodes[other].depends_on.includes(id) && !(node.taskKind === "loop" && workspace.nodes[other].taskKind === "wait"))];
  const task = node.task;
  return html`
    ${node.context ? html`<button @click=${() => openOrigin(node.origin)}>
      ${t("activity.open", {kind: t(`mode.${node.taskKind}`)})}</button>` : nothing}
    ${node.summary ? html`<h4>${t("workspace.context")}</h4><p class="task-note">${node.summary}</p>` : nothing}
    ${node.taskKind === "loop" ? loopHistory(node) : nothing}
    ${related.length ? html`<h4>${t("activity.related")}</h4><div class="node-dependencies">
      ${related.map(other => html`<button @click=${() => showNode(other)}>${workspace.nodes[other].title}</button>`)}
    </div>` : nothing}
    ${task && !task.error ? activityMetadata(node, task) : nothing}
  `;
}

function activityMetadata(node, task) {
  const isWait = node.taskKind === "wait";
  const cancellable = !node.context && (isWait ? task.state === "active" && !task.owner : task.status === "active");
  const detail = isWait ? {result: task.result, log_file: task.log_file}
    : {path: task.path, goal: task.goal, runs: task.runs, events: task.events};
  return html`
    ${task.owner ? html`<p class="muted">${t("activity.managed")}</p>` : nothing}
    <div class="task-meta">
      <span>${t("field.deadline", {value: formatTime(task.deadline_at)})}</span>
      ${isWait ? html`<span>${t("field.query", {value: task.query_status || "—"})}</span>
        <span>${t("field.notification", {value: task.result?.notification || task.notification_channel?.status || "—"})}</span>`
        : html`<span>${t("field.iteration", {value: task.iteration + (task.max_iterations ? " / " + task.max_iterations : "")})}</span>
          <span>${t("field.interval", {value: task.interval_seconds})}</span>
          ${task.next_run_at ? html`<span>${t("field.next", {value: formatTime(task.next_run_at)})}</span>` : nothing}`}
    </div>
    ${cancellable ? html`<button class="danger" @click=${() => cancelTask(task, node.taskKind)}>${t("common.cancel")}</button>` : nothing}
    <details class="task-more"><summary>${t("common.details")}</summary><pre>${JSON.stringify(detail, null, 2)}</pre></details>
  `;
}

function goalDetails(goal, node, id) {
  const relatedLoops = tasks.loops.filter(loop => loop.goal?.state === goal.path && loop.goal?.node === id);
  const waits = tasks.linked_watches.filter(watch =>
    watch.owner.mode === "goal" && watch.owner.path === goal.path && watch.owner.node === id);
  const result = typeof node.result === "string" ? node.result : node.result?.summary || JSON.stringify(node.result);
  return html`
    <h4>${t("details.dependencies")}</h4>
    <div class="node-dependencies">
      ${node.depends_on.length ? node.depends_on.map(dependency => html`
        <button title=${goal.nodes[dependency].title} @click=${() => showNode(dependency)}>${dependency}</button>`)
        : html`<span class="muted">${t("field.noDependencies")}</span>`}
    </div>
    ${node.acceptance.length ? html`<b>${t("field.acceptance")}</b><ul>
      ${node.acceptance.map(item => html`<li>${item}</li>`)}
    </ul>` : nothing}
    ${node.wait || relatedLoops.length || waits.length ? html`<section class="wait-context">
      <b>${t("field.waitLoop")}</b>
      ${node.wait ? html`<p class="task-note">${node.wait.label || t("field.externalCondition")}</p>
        ${node.wait.event_note ? html`<p class="task-note">${t("field.triggered", {value: node.wait.event_note})}</p>` : nothing}` : nothing}
      ${waits.map(wait => html`<p class="muted">${t(`state.${wait.state}`)} · ${t("field.deadline", {value: formatTime(wait.deadline_at)})}</p>`)}
      ${relatedLoops.map(loop => html`
        <p class="task-note">${t("field.loopState", {task: loop.task, iteration: loop.iteration, status: t(`state.${loop.status}`)})}</p>
        ${loop.event_note ? html`<p class="task-note">${t("field.loopNext", {value: loop.event_note})}</p>` : nothing}`)}
    </section>` : nothing}
    ${node.result ? html`<h4>${t("field.result")}</h4><p class="task-note">${result}</p>` : nothing}
    ${node.wait ? html`<details class="muted"><summary>${t("field.waitDetails")}</summary>
      <pre>${JSON.stringify(node.wait, null, 2)}</pre></details>` : nothing}
    ${node.status === "failed" && goal.status === "open" ? html`<button @click=${() => act("retry", {id})}>
      ${t("common.retry")}</button>` : nothing}
    ${node.status === "pending" && goal.status === "open" ? editForm(node, id) : nothing}
  `;
}

function renderDetails(goal, timings) {
  const id = nodeId;
  const node = goal.nodes[id];
  const kind = node?.taskKind ? t(`mode.${node.taskKind}`) : node && t(`field.nodeKind.${node.kind}`);
  renderTemplate(html`<span class="eyebrow">${t("details.title")}</span>
    ${node ? keyed(JSON.stringify([mode, goal.path, id]), html`
      ${nodeBadge(goal, node)}
      <h3>${node.title}</h3><p class="muted">${node.shortId || id} · ${kind}</p>
      ${nodeTimeline(timings.get(id))}
      ${node.taskKind ? activityDetails(goal, node, id) : goalDetails(goal, node, id)}
    `) : html`<p class="muted">${t("field.selectNode")}</p>`}`, $("details"));
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
  if (taskView === "graph") viewport.reset();
  render();
}
$("taskScope").onchange = () => {
  statusFilter.clear();
  applyFilter();
};
$("nodeSearch").oninput = applyFilter;
$("nodeMetrics").onclick = event => {
  const button = event.target.closest("button[data-state]");
  if (!button) return;
  statusFilter.toggle(button.dataset.state);
};
function filterStatuses() {
  $("taskScope").value = scopeForStatuses(statusFilter.selected, $("taskScope").value);
  applyFilter();
}
$("nodeColumns").onclick = event => {
  const button = event.target.closest("[data-sort]");
  if (!button) return;
  sort = nextSort(sort, button.dataset.sort);
  renderWorkspace(currentWorkspace());
};
$("nodeViews").onclick = event => {
  const button = event.target.closest("button[data-view]");
  if (button) { taskView = button.dataset.view; render(); }
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
    const visible = [...root.querySelectorAll(".node-row")].map(row => row.dataset.node);
    const included = new Set(visible);
    const timings = nodeTimings(goal);
    const ordered = orderRows(nodeRows(goal).filter(row => included.has(row.id)), timings, sort, now);
    if (ordered.some(({id}, index) => id !== visible[index])) {
      const focused = document.activeElement;
      renderRows(goal, ordered, timings);
      if (root.contains(focused) && document.activeElement !== focused) focused.focus({preventScroll: true});
    }
  }
}
setInterval(refreshDurations, 1000);
document.addEventListener("visibilitychange", refreshDurations);

const stream = new EventSource("/events" + scopeQuery);
stream.onmessage = event => {
  tasks = JSON.parse(event.data);
  translate($("connection"), "connection.live");
  render(focusPending);
  if (focusPending) {
    document.getElementById("focusedTask")?.scrollIntoView({block: "center", behavior: "smooth"});
    focusPending = false;
  }
};
stream.onerror = () => translate($("connection"), "connection.reconnecting");
