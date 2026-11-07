import {createAppearance} from "./appearance.mjs";
import {layoutGraph} from "./graph-layout.mjs";
import {nodeRows, nodeState, nodeTimings} from "./task-model.mjs";

const $ = id => document.getElementById(id);
const scope = new URLSearchParams(location.search);
const scopeQuery = "?" + scope.toString();
const allSessions = scope.get("all") === "1";
$("scopeLabel").dataset.i18n = allSessions ? "scope.all" : "scope.current";
if (scope.get("session")) $("scopeLabel").title = scope.get("session");
else $("scopeLabel").dataset.i18nTitle = "scope.openHint";
$("scopeSwitch").dataset.i18n = allSessions ? "scope.current" : "scope.all";
const otherScope = new URLSearchParams(scope);
if (allSessions) otherScope.delete("all"); else otherScope.set("all", "1");
$("scopeSwitch").href = "/?" + otherScope.toString();
$("scopeSwitch").hidden = allSessions && !scope.get("session");
let tasks = {goals: [], watches: [], loops: [], linked_watches: []};
let mode = scope.get("mode") || "session";
const focus = scope.get("focus") || "";
let focusPending = !!focus;

let selected = mode === "goal" ? focus : "";
let nodeId = scope.get("node") || "";
let taskView = scope.get("view") === "graph" ? "graph" : "list";
let graphZoom = 1;

const { t, formatTime, translate } = createAppearance(() => render());

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

function currentGoal() {
  return tasks.goals.find(goal => goal.path === selected);
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

function renderTaskList(collection, kind, list) {
  if (!collection.length) {
    const empty = element("div", undefined, "card empty");
    empty.append(element("h2", t(kind === "wait" ? "empty.wait" : "empty.loop")));
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...collection.map(task => {
    const identity = kind === "wait" ? task.watch_id : task.path;
    const card = element("article", undefined, "card task-card" + (identity === focus ? " focused" : ""));
    if (identity === focus) card.id = "focusedTask";
    if (task.error) {
      card.append(element("p", task.error));
      return card;
    }
    const state = kind === "wait" ? task.state : task.status;
    const heading = element("div", undefined, "task-heading");
    heading.append(element("h3", kind === "wait" ? task.label : task.task),
      statusBadge(state));
    if (state === "active") {
      const cancel = element("button", t("common.cancel"), "danger");
      cancel.onclick = () => cancelTask(task, kind);
      heading.append(cancel);
    }
    card.append(heading);
    const meta = element("div", undefined, "task-meta");
    if (kind === "loop") {
      let phase = t(`state.${state}`);
      if (task.phase === "waiting") phase = t("field.next", {value: formatTime(task.next_run_at)});
      else if (task.phase === "running") phase = t("state.running");
      meta.append(
        element("span", t("field.iteration", {value: task.iteration + (task.max_iterations ? " / " + task.max_iterations : "")})),
        element("span", t("field.interval", {value: task.interval_seconds})),
        element("span", phase)
      );
    } else {
      meta.append(element("span", task.watch_id.slice(0, 8)),
        element("span", t("field.query", {value: task.query_status || "—"})),
        element("span", t("field.notification", {value: task.result?.notification || task.notification_channel?.status || "—"})));
    }
    meta.append(element("span", t("field.deadline", {value: formatTime(task.deadline_at)})));
    if (allSessions) meta.append(element("span", t("field.session", {value: task.session})));
    card.append(meta);
    if (task.event_note) {
      card.append(element("p", t("field.triggered", {value: task.event_note}), "task-note"));
    }
    const more = element("details");
    more.append(element("summary", t("common.details")));
    const details = kind === "wait"
      ? {result: task.result, log_file: task.log_file}
      : {path: task.path, goal: task.goal, runs: task.runs, events: task.events};
    more.append(element("pre", JSON.stringify(details, null, 2)));
    card.append(more);
    return card;
  }));
}

function renderSummary() {
  const validGoals = tasks.goals.filter(goal => !goal.error);
  const running = validGoals.some(goal => Object.values(goal.nodes).some(node => ["running", "dispatching"].includes(node.status))) ||
    tasks.loops.some(loop => loop.status === "active" && loop.phase === "running");
  const ready = validGoals.some(goal => goal.status === "open" && goal.activity === "ready");
  const waiting = tasks.watches.filter(watch => watch.state === "active").length +
    tasks.linked_watches.filter(watch => {
      if (watch.state !== "active") return false;
      if (watch.owner.mode === "goal") {
        const goal = validGoals.find(goal => goal.path === watch.owner.path);
        return goal?.status === "open" && goal.nodes[watch.owner.node]?.wait?.watch_id === watch.watch_id;
      }
      const loop = tasks.loops.find(loop => loop.path === watch.owner.path);
      return loop?.status === "active" && loop.watch_id === watch.watch_id;
    }).length;
  const paused = validGoals.some(goal => goal.status === "paused");
  const verification = validGoals.some(goal => goal.status === "open" && goal.activity === "awaiting_verification");
  const failed = validGoals.some(goal => Object.values(goal.nodes).some(node => node.status === "failed")) ||
    tasks.watches.some(watch => watch.state === "failed");
  const statuses = [
    [failed, "status.attention"],
    [paused, "status.paused"],
    [running, "status.running"],
    [waiting, "status.waiting"],
    [verification, "status.verification"],
    [ready, "status.ready"],
    [validGoals.length || tasks.watches.length || tasks.loops.length, "status.idle"],
    [true, "status.start"]
  ];
  const guidance = [
    [paused, "guide.paused"],
    [waiting, "guide.waiting", {count: waiting}],
    [failed, "guide.failed"],
    [verification, "guide.verification"],
    [running || ready, "guide.progress"],
    [true, "guide.idle"]
  ];
  translate($("sessionStatus"), statuses.find(([matches]) => matches)[1]);
  const [, key, values = {}] = guidance.find(([matches]) => matches);
  $("sessionNext").textContent = t(key, values);
}

function renderIndependent() {
  const root = $("independentTasks");
  root.replaceChildren();
  root.hidden = mode !== "session";
  if (root.hidden) return;
  const finishedGoals = tasks.goals.filter(goal => ["completed", "cancelled"].includes(goal.status));
  if (finishedGoals.length) {
    const history = element("details", undefined, "task-history");
    history.append(element("summary", t("history.goals", {count: finishedGoals.length})));
    for (const goal of finishedGoals) {
      const button = element("button", goal.objective);
      button.onclick = () => { selected = goal.path; nodeId = ""; mode = "goal"; render(); };
      history.append(button);
    }
    root.append(history);
  }
  const independent = [
    ["wait", tasks.watches],
    ["loop", tasks.loops.filter(loop => !loop.goal)]
  ];
  for (const [kind, collection] of independent) {
    const past = [];
    const active = [];
    for (const task of collection) {
      const status = kind === "wait" ? task.state : task.status;
      (task.error || status !== "active" ? past : active).push(task);
    }
    if (active.length) {
      root.append(element("h3", t(kind === "wait" ? "section.independentWaits" : "section.independentLoops"), "context-heading"));
      const list = element("div");
      renderTaskList(active, kind, list);
      root.append(list);
    }
    if (past.length) {
      const history = element("details", undefined, "task-history");
      history.append(element("summary", t(kind === "wait" ? "history.waits" : "history.loops", {count: past.length})));
      const list = element("div");
      renderTaskList(past, kind, list);
      history.append(list);
      root.append(history);
    }
  }
}

function render() {
  renderSummary();
  renderIndependent();
  const counts = {session: tasks.goals.filter(goal => goal.status === "open").length +
    tasks.watches.filter(task => task.state === "active").length +
    tasks.loops.filter(task => task.status === "active" && !task.goal).length,
    goal: tasks.goals.length, wait: tasks.watches.length, loop: tasks.loops.length};
  for (const button of $("modes").querySelectorAll("button")) {
    const kind = button.dataset.mode;
    button.textContent = `${t(`mode.${kind}`)} · ${counts[kind]}`;
    button.setAttribute("aria-selected", kind === mode);
  }
  const showingGoal = ["session", "goal"].includes(mode);
  $("goalToolbar").hidden = !showingGoal || !tasks.goals.length;
  $("taskList").hidden = showingGoal;
  if (!showingGoal) {
    $("empty").hidden = true;
    $("content").hidden = true;
    renderTaskList(mode === "wait" ? tasks.watches : tasks.loops, mode, $("taskList"));
    return;
  }
  if (!currentGoal()) selected = (tasks.goals.find(goal => goal.status === "open") || tasks.goals[0])?.path || "";
  $("goals").replaceChildren(...tasks.goals.map(goal => {
    const option = element("option", goal.objective?.trim().split(/[;；\n]/)[0] || goal.objective || goal.path);
    option.value = goal.path;
    return option;
  }));
  $("goals").value = selected;

  const goal = currentGoal();
  const historical = mode === "session" && goal && ["completed", "cancelled"].includes(goal.status);
  $("empty").hidden = !!goal || mode === "session";
  $("content").hidden = !goal || historical;
  $("goalToolbar").hidden = historical || !goal;
  for (const action of ["pause", "resume", "cancel"]) $(action).disabled = !goal;
  if (!goal) return;
  if (goal.error) {
    showError(goal.error);
    $("content").hidden = true;
    return;
  }

  $("objective").textContent = goal.objective.trim().split(/[;；\n]/)[0] || goal.objective;
  $("objective").title = goal.objective;
  $("goalDescription").textContent = goal.objective;
  const available = {
    pause: goal.status === "open",
    resume: goal.status === "paused",
    cancel: ["open", "paused"].includes(goal.status)
  };
  for (const [action, enabled] of Object.entries(available)) {
    $(action).hidden = !enabled;
    $(action).disabled = !enabled;
  }
  $("add").disabled = goal.status !== "open";

  const nodes = Object.values(goal.nodes);
  const completed = nodes.filter(node => node.status === "completed").length;
  $("stats").replaceChildren(
    statusBadge(goal.status),
    element("span", t("field.completeCount", {done: completed, total: nodes.length}))
  );
  $("progress").style.width = (nodes.length ? completed / nodes.length * 100 : 0) + "%";
  const timings = nodeTimings(goal);
  renderWorkspace(goal, timings);
  const details = $("details");
  const editor = details.querySelector("form");
  const node = goal.nodes[nodeId];
  const editable = node?.status === "pending" && goal.status === "open";
  const sameTask = editor?.dataset.node === nodeId && editor?.dataset.goal === goal.path;
  if (!editable || !sameTask || !editor.contains(document.activeElement)) {
    renderDetails(goal, editable && sameTask ? editor : null, timings);
  } else {
    details.querySelector("h3").textContent = node.title;
    details.querySelector(".badge").replaceWith(statusBadge(nodeState(goal, node)));
    details.querySelector(".node-timeline").replaceWith(nodeTimeline(timings.get(nodeId)));
  }
  renderEvents(goal.events);
}

function statusBadge(state) {
  const badge = element("span", t(`state.${state}`), "badge");
  badge.dataset.state = state;
  return badge;
}

function taskContext(goal, node) {
  const state = nodeState(goal, node);
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

function timeCell(date) {
  const cell = element("span", undefined, "node-time");
  cell.append(element("span", t("time.updated"), "time-caption"),
    timeElement(date, {hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"}));
  if (date) cell.append(element("span", formatTime(date, {month: "short", day: "numeric"}), "time-date"));
  return cell;
}

function nodeTimeline(timing) {
  const timeline = element("dl", undefined, "node-timeline");
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
  const goal = currentGoal();
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
  const rows = nodeRows(goal);
  if (!Object.hasOwn(goal.nodes, nodeId)) nodeId = rows[0]?.id || "";
  const counts = {};
  for (const {state} of rows) counts[state] = (counts[state] || 0) + 1;
  const filter = $("nodeFilter").value;
  const search = $("nodeSearch").value.trim().toLowerCase();
  const matching = rows.filter(({id, node, state}) =>
    (filter === "all" || state === filter) &&
    `${id} ${node.title}`.toLowerCase().includes(search));
  for (const option of $("nodeFilter").options) {
    const state = option.value;
    const label = t(state === "all" ? "workspace.all" : `state.${state}`);
    const count = counts[state] || 0;
    option.textContent = state === "all" ? label : `${label} · ${count}`;
  }
  $("nodeCount").textContent = t("workspace.count", {shown: matching.length, total: rows.length});
  for (const button of $("nodeMetrics").querySelectorAll("button")) {
    button.querySelector("strong").textContent = counts[button.dataset.state] || 0;
    button.setAttribute("aria-pressed", filter === button.dataset.state);
  }
  for (const button of $("nodeViews").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", button.dataset.view === taskView);
  }
  $("nodeList").hidden = taskView !== "list";
  $("graph").hidden = taskView !== "graph";
  $("graphFooter").hidden = taskView !== "graph";
  if (taskView === "graph") {
    draw(goal, new Set(matching.map(({id}) => id)));
    return;
  }
  if (!matching.length) {
    const empty = element("div", undefined, "empty");
    empty.append(
      element("h3", t(rows.length ? "workspace.empty" : "workspace.emptyGoal")),
      element("p", t(rows.length ? "workspace.emptyHint" : "workspace.addFirst"), "muted")
    );
    $("nodeList").replaceChildren(empty);
    return;
  }
  const columns = element("div", undefined, "node-columns");
  columns.setAttribute("aria-hidden", "true");
  columns.append(...["workspace.task", "workspace.state", "workspace.context", "time.updated"].map(key => element("span", t(key))));
  $("nodeList").replaceChildren(columns, ...matching.map(({id, node, state}) => {
    const button = element("button", undefined, "node-row");
    button.dataset.node = id;
    button.title = node.title;
    button.setAttribute("aria-pressed", id === nodeId);
    const name = element("span", undefined, "node-name");
    name.append(element("strong", node.title), element("code", id));
    const status = element("span");
    status.append(statusBadge(state));
    button.append(name, status, element("span", taskContext(goal, node), "node-reason"), timeCell(timings.get(id).updated));
    button.onclick = () => showNode(id);
    return button;
  }));
}

function draw(goal, matching) {
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
      opacity: matching.has(id) ? 1 : .23
    });
    group.append(
      svgElement("rect", {width: 280, height: 114, rx: 10,
        fill: selectedNode ? "var(--node-selected)" : "var(--node)",
        stroke: selectedNode ? "var(--node-selected-border)" : "var(--node-border)", "stroke-width": selectedNode ? 2 : 1}),
      svgElement("rect", {x: 0, y: 14, width: 4, height: 86, rx: 2, fill: colors[nodeState(goal, node)]})
    );
    const content = svgElement("foreignObject", {x: 16, y: 11, width: 249, height: 98});
    const box = element("div", undefined, "graph-node");
    box.append(element("strong", node.title), element("code", id), statusBadge(nodeState(goal, node)));
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
  $("graph").replaceChildren(svg);
  applyZoom();
}

function applyZoom() {
  const svg = $("graph").querySelector("svg");
  if (!svg) return;
  const bounds = svg.viewBox.baseVal;
  svg.style.width = bounds.width * graphZoom + "px";
  svg.style.height = bounds.height * graphZoom + "px";
  $("zoomLevel").textContent = Math.round(graphZoom * 100) + "%";
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
  const kind = t(`field.nodeKind.${node.kind}`);
  box.append(
    statusBadge(nodeState(goal, node)),
    element("h3", node.title),
    element("p", nodeId + " · " + kind, "muted")
  );
  box.append(nodeTimeline(timings.get(nodeId)));
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

$("goals").onchange = event => {
  selected = event.target.value;
  nodeId = "";
  $("nodeSearch").value = "";
  $("nodeFilter").value = "all";
  graphZoom = 1;
  render();
};
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
  if (button) { mode = button.dataset.mode; render(); }
};

$("nodeSearch").oninput = () => renderWorkspace(currentGoal());
$("nodeMetrics").onclick = event => {
  const button = event.target.closest("button[data-state]");
  if (!button) return;
  const filter = $("nodeFilter");
  filter.value = filter.value === button.dataset.state ? "all" : button.dataset.state;
  renderWorkspace(currentGoal());
};
$("nodeFilter").onchange = () => renderWorkspace(currentGoal());
$("nodeViews").onclick = event => {
  const button = event.target.closest("button[data-view]");
  if (button) { taskView = button.dataset.view; renderWorkspace(currentGoal()); }
};
$("zoomIn").onclick = () => { graphZoom = Math.min(2, graphZoom * 1.2); applyZoom(); };
$("zoomOut").onclick = () => { graphZoom = Math.max(.15, graphZoom / 1.2); applyZoom(); };
$("fitGraph").onclick = () => {
  const bounds = $("graph").querySelector("svg").viewBox.baseVal;
  graphZoom = Math.min(1, ($("graph").clientWidth - 16) / bounds.width, ($("graph").clientHeight - 16) / bounds.height);
  applyZoom();
  $("graph").scrollTo(0, 0);
};

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
