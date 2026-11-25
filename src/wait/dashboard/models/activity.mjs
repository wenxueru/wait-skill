import {nodeState, nodeTimings, taskScope} from "./tasks.mjs";

export function activityId(mode, identity, node = null) {
  return JSON.stringify([mode, identity, node]);
}

export function watchState(watch) {
  if (watch.error) return "failed";
  if (watch.state === "active") {
    return ["running", "querying"].includes(watch.phase) && !watch.result ? "running" : "waiting";
  }
  const event = watch.result?.event;
  if (event === "cancelled" || event === "activation_cancelled" || watch.result?.notification === "cancelled") {
    return "cancelled";
  }
  return watch.state;
}

export function wakeStatus({state, result}) {
  const notification = result?.notification;
  if (state === "cancelled" || !notification) return null;
  switch (notification) {
    case "cancelled":
    case "disabled":
      return null;
    case "started":
    case "completed":
      return {status: "started"};
    case "queued":
    case "unconfirmed":
      return {status: "review", hint: notification};
    case "native_pending":
      return {status: "pending", hint: notification};
  }
  if (state === "active" && (notification === "pending" || notification === "attempting" || notification.endsWith("_retrying"))) {
    return {status: "pending", retryAt: result.next_notification_at};
  }
  return {status: "review", hint: "failed"};
}

function retiredWatchIds({goals, loops}, watches) {
  const owners = new Map();
  for (const {watch_id, owner} of watches) {
    if (owner) owners.set(watch_id, owner);
  }
  const retired = new Set();
  if (!owners.size) return retired;
  function retire(id, mode, path, node = null) {
    const owner = owners.get(id);
    if (owner?.mode === mode && owner.path === path && (owner.node ?? null) === node) retired.add(id);
  }
  for (const goal of goals) {
    if (!goal.events) continue;
    const closed = taskScope(goal.status) === "history";
    const newerWaits = new Map();
    for (let index = goal.events.length - 1; index >= 0; index--) {
      const {operation, node_id, details} = goal.events[index];
      if (!details) continue;
      if (operation === "wake_node") retire(details.event_id, "goal", goal.path, node_id);
      if (operation === "abort_wait") retire(details.watch_id, "goal", goal.path, node_id);
      if (operation === "activate_wait" && !newerWaits.has(node_id)) {
        newerWaits.set(node_id, details.watch_id);
      }
      if (operation === "prepare_wait") {
        const newerWait = newerWaits.get(node_id);
        if (closed || taskScope(goal.nodes[node_id]?.status) === "history" || (newerWait && newerWait !== details.watch_id)) {
          retire(details.watch_id, "goal", goal.path, node_id);
        }
      }
    }
  }
  for (const loop of loops) {
    for (const {operation, event_id, watch_id} of loop.events ?? []) {
      if (operation === "begin" || operation === "expire") retire(event_id, "loop", loop.path);
      if (operation === "schedule" && watch_id !== loop.watch_id) retire(watch_id, "loop", loop.path);
    }
  }
  return retired;
}

export function watchActivity(watch, retired = false) {
  const state = watchState(watch);
  const wake = wakeStatus(watch);
  const review = wake?.status === "review" && wake.hint !== "queued"
    ? `wake.hint.${wake.hint}`
    : state === "failed" ? "attention.waitFailed" : null;
  let scope = taskScope(state);
  if (watch.state === "active") scope = "current";
  else if (retired) scope = "history";
  else if (state !== "cancelled") {
    if (review) scope = "attention";
    else if (wake?.status === "pending") scope = "current";
  }
  return {scope, state, wake, review};
}

function summarizeSession({goals, loops}, watches) {
  const readableGoals = goals.filter(goal => !goal.error);
  const attention = [];
  let offline = 0;
  const workspaceCounts = {
    wait: 0,
    loop: loops.filter(loop => loop.status === "active").length,
    goal: readableGoals.filter(goal => ["open", "paused"].includes(goal.status)).length
  };
  let runningWatch = false;
  let unreadableWatch = false;
  for (const [watch, {scope, state, wake, review}] of watches) {
    if (scope === "current") workspaceCounts.wait++;
    runningWatch ||= state === "running";
    unreadableWatch ||= !!watch.error;
    if (wake?.status === "pending" && watch.result.notification === "notification_unavailable_retrying") {
      offline++;
    }
    if (scope === "attention" && review && !watch.error) {
      attention.push({task: watch, origin: {mode: "wait", watch_id: watch.watch_id}, title: watch.label, reason: review});
    }
  }
  for (const goal of readableGoals) {
    if (goal.status !== "open") continue;
    const origin = {mode: "goal", path: goal.path};
    const failed = Object.entries(goal.nodes).filter(([, node]) => node.status === "failed");
    for (const [id, node] of failed) {
      attention.push({task: goal, origin: {...origin, node: id}, title: node.title, reason: "attention.goalFailed"});
    }
    if (goal.activity === "orphaned_wait" || (!failed.length && goal.activity === "blocked")) {
      const reason = goal.activity === "orphaned_wait" ? "attention.missingWatcher" : "attention.goalBlocked";
      attention.push({task: goal, origin, title: goal.objective, reason});
    }
  }
  const unavailable = unreadableWatch || goals.some(goal => goal.error) || loops.some(loop => loop.error);
  const running = readableGoals.some(goal => Object.values(goal.nodes).some(node => ["running", "dispatching"].includes(node.status))) ||
    loops.some(loop => loop.status === "active" && loop.phase === "running") ||
    runningWatch;
  const states = [
    [attention.length || unavailable, "attention"],
    [readableGoals.some(goal => goal.status === "paused"), "paused"],
    [running, "running"],
    [workspaceCounts.wait, "waiting"],
    [readableGoals.some(goal => goal.status === "open" && goal.activity === "awaiting_verification"), "verification"],
    [readableGoals.some(goal => goal.status === "open" && goal.activity === "ready"), "ready"],
    [readableGoals.length || watches.size || loops.length, "idle"]
  ];
  const status = states.find(([matches]) => matches)?.[1] ?? "start";
  return {status, attention, offline, unavailable, workspaceCounts};
}

export function sessionActivity(tasks) {
  const watches = [...tasks.watches, ...tasks.linked_watches];
  const retired = retiredWatchIds(tasks, watches);
  const visibility = new Map(watches.map(watch => [watch, watchActivity(watch, retired.has(watch.watch_id))]));
  const workspaces = new Map();
  return {
    ...tasks,
    summary: summarizeSession(tasks, visibility),
    watchScope: watch => visibility.get(watch)?.scope,
    workspace(mode) {
      if (!workspaces.has(mode)) workspaces.set(mode, activityWorkspace(tasks, mode, visibility));
      return workspaces.get(mode);
    }
  };
}

export function relationNodes(nodes, selected) {
  const result = {};
  function include(id) {
    if (Object.hasOwn(result, id)) return;
    result[id] = nodes[id];
    for (const parent of nodes[id].depends_on) include(parent);
  }
  for (const id of selected) include(id);
  for (const [id, node] of Object.entries(nodes)) {
    if (node.context && node.taskKind === "wait" && node.depends_on.some(parent => selected.has(parent))) include(id);
  }
  return result;
}

function loopNode(loop, context) {
  const events = loop.events || [];
  const last = events.at(-1);
  const status = loop.error ? "failed" : loop.status === "active" ? loop.phase : loop.status;
  const phase = events.findLast(event => ["init", "begin", "schedule"].includes(event.operation));
  return {
    title: loop.task || loop.path.split("/").at(-1), status, taskKind: "loop", kind: "external", context,
    shortId: loop.path.split("/").at(-1), depends_on: [], acceptance: [],
    origin: {mode: "loop", path: loop.path}, task: loop, summary: loop.error || loop.event_note,
    created_at: events[0]?.at, started_at: events[0]?.at, updated_at: last?.at,
    phase_started_at: phase?.at,
    ...(status === "waiting" ? {wait: {since: phase?.at}} : {}),
    ...(["completed", "cancelled"].includes(status) ? {completed_at: last?.at} : {})
  };
}

// These nodes are a browser view of recorded ownership. They never enter scheduler state.
function activityWorkspace(tasks, mode, visibility) {
  const nodes = {};
  const goals = new Map(tasks.goals.map(goal => [goal.path, goal]));
  const goalTimings = new Map();
  const loops = new Map(tasks.loops.map(loop => [loop.path, loop]));

  function goalContext(path, nodeId) {
    const id = activityId("goal", path, nodeId);
    if (Object.hasOwn(nodes, id)) return id;
    const goal = goals.get(path);
    const source = goal?.nodes?.[nodeId];
    nodes[id] = {
      ...source, title: source?.title || goal?.objective || path.split("/").at(-1),
      status: source ? nodeState(goal, source) : "pending", kind: source?.kind || "external",
      taskKind: "goal", context: true, shortId: nodeId || path.split("/").at(-1), acceptance: [],
      depends_on: [], origin: {mode: "goal", path, node: nodeId}, summary: goal?.objective
    };
    if (source) {
      if (!goalTimings.has(path)) goalTimings.set(path, nodeTimings(goal));
      const timing = goalTimings.get(path).get(nodeId);
      Object.assign(nodes[id], {
        created_at: timing.created, updated_at: timing.updated,
        phase_started_at: timing.phaseStarted, completed_at: timing.finished
      });
      nodes[id].depends_on = source.depends_on.map(parent => goalContext(path, parent));
    }
    return id;
  }

  function addLoop(path, context) {
    const id = activityId("loop", path);
    if (Object.hasOwn(nodes, id)) {
      if (!context) nodes[id].context = false;
      return id;
    }
    const source = loops.get(path);
    nodes[id] = source ? loopNode(source, context) : {
      title: path.split("/").at(-1), status: "pending", kind: "external", taskKind: "loop",
      context: true, shortId: path.split("/").at(-1), depends_on: [], acceptance: [], origin: {mode: "loop", path}
    };
    if (source?.goal) nodes[id].depends_on = [goalContext(source.goal.state, source.goal.node)];
    return id;
  }

  function addWatch(watch, context) {
    const identity = watch.watch_id || watch.path;
    const id = activityId("wait", identity);
    const {state: status, scope} = visibility.get(watch);
    nodes[id] = {
      title: watch.label || identity, status, scope,
      taskKind: "wait", kind: "external", context,
      shortId: identity.slice(0, 8), depends_on: [], acceptance: [],
      origin: {mode: "wait", watch_id: identity}, task: watch, summary: watch.error || watch.event_note,
      created_at: watch.submitted_at, started_at: watch.submitted_at, updated_at: watch.updated_at,
      ...(status === "waiting" ? {wait: {since: watch.result?.available_at ?? watch.submitted_at}} : {}),
      ...(["completed", "failed", "cancelled"].includes(status) ? {completed_at: watch.updated_at} : {})
    };
    if (watch.owner?.mode === "goal") nodes[id].depends_on = [goalContext(watch.owner.path, watch.owner.node)];
    if (watch.owner?.mode === "loop") nodes[id].depends_on = [addLoop(watch.owner.path, true)];
    return id;
  }

  if (mode === "wait") {
    for (const watch of visibility.keys()) addWatch(watch, false);
  } else {
    const watchById = new Map();
    for (const watch of visibility.keys()) watchById.set(watch.watch_id, watch);
    for (const loop of tasks.loops) {
      addLoop(loop.path, false);
      const timer = watchById.get(loop.watch_id);
      if (timer) addWatch(timer, true);
    }
  }
  return {path: mode, status: "open", nodes, events: []};
}

// Timer records remain in lists/state; only the graph folds them into their owner.
export function collapseLoopTimers(nodes) {
  const graph = {...nodes};
  const members = new Map();
  for (const [id, node] of Object.entries(nodes)) {
    if (node.taskKind !== "wait" || node.task?.owner?.mode !== "loop") continue;
    const owner = node.depends_on.find(parent => nodes[parent]?.taskKind === "loop");
    if (!owner) continue;
    if (graph[owner].timerCount === undefined) graph[owner] = {...graph[owner], timerCount: 0, failedTimers: 0};
    graph[owner].timerCount++;
    if (node.status === "failed") graph[owner].failedTimers++;
    members.set(id, owner);
    delete graph[id];
  }
  return {nodes: graph, members};
}
