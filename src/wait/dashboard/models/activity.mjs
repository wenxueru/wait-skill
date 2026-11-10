import {nodeState, nodeTimings} from "./tasks.mjs";

export function activityId(mode, identity, node = null) {
  return JSON.stringify([mode, identity, node]);
}

export function watchState(watch) {
  if (watch.state !== "active") return watch.state;
  return ["running", "querying"].includes(watch.phase) && !watch.result ? "running" : "waiting";
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
export function activityWorkspace(tasks, mode) {
  const nodes = {};
  const goals = new Map(tasks.goals.map(goal => [goal.path, goal]));
  const goalTimings = new Map();
  const loops = new Map(tasks.loops.map(loop => [loop.path, loop]));
  const watches = [...tasks.watches, ...tasks.linked_watches];

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
    const status = watch.error ? "failed" : watchState(watch);
    nodes[id] = {
      title: watch.label || identity, status, taskKind: "wait", kind: "external", context,
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
    for (const watch of watches) addWatch(watch, false);
  } else {
    const watchById = new Map(watches.map(watch => [watch.watch_id, watch]));
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
