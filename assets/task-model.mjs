export function nodeState(goal, node) {
  if (node.taskKind) return node.status;
  if (node.status === "dispatching") return "running";
  if (node.status !== "pending") return node.status;
  if (node.depends_on.some(id => goal.nodes[id].status !== "completed")) return "blocked";
  return goal.status === "open" ? "ready" : "pending";
}

export function nodeRows(goal) {
  const priority = ["failed", "running", "ready", "waiting", "blocked", "pending", "completed", "cancelled"];
  return Object.entries(goal.nodes)
    .filter(([, node]) => !node.context)
    .map(([id, node]) => ({id, node, state: nodeState(goal, node)}))
    .sort((a, b) => priority.indexOf(a.state) - priority.indexOf(b.state));
}

export function timeDate(value) {
  if (value == null) return null;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function nextSort(current, key) {
  if (current?.key !== key) return {key, direction: "desc"};
  if (current.direction === "desc") return {key, direction: "asc"};
  return null;
}

export function orderRows(rows, timings, sort, now = Date.now()) {
  if (!sort) return rows;
  const values = new Map(rows.map(row => {
    const timing = timings.get(row.id);
    if (sort.key === "updated") return [row.id, timing.updated === null ? null : +timing.updated];
    const {phase, total} = durationRanges(timing);
    const range = phase || total;
    return [row.id, range ? Math.max(0, (range.end || now) - range.start) : null];
  }));
  return [...rows].sort((a, b) => {
    const left = values.get(a.id), right = values.get(b.id);
    if (left === null || right === null) return Number(left === null) - Number(right === null);
    return sort.direction === "asc" ? left - right : right - left;
  });
}

export function nodeTimings(goal) {
  const timings = new Map();
  const phases = {
    add_node: "pending", retry_node: "pending", abort_agent: "pending", prepare_agent: "dispatching",
    start_node: "running", wake_node: "running", abort_wait: "running", activate_wait: "waiting"
  };
  for (const [id, node] of Object.entries(goal.nodes)) {
    const timing = {updated: null, status: node.status, created: timeDate(node.created_at), phaseStarted: timeDate(node.phase_started_at)};
    for (const [key, value] of [
      ["started", node.started_at], ["waiting", node.wait?.since],
      ["finished", node.completed_at ?? node.failed_at], ["received", node.last_event?.at],
      ["assigned", node.assignment?.assigned_at ?? node.assignment?.prepared_at], ["updated", node.updated_at]
    ]) {
      const date = timeDate(value);
      if (!date) continue;
      timing[key] = date;
      if (!timing.updated || date > timing.updated) timing.updated = date;
    }
    timings.set(id, timing);
  }
  for (const event of goal.events) {
    const date = timeDate(event.at);
    if (!date) continue;
    const timing = timings.get(event.node_id);
    if (timing) {
      if (!timing.updated || date > timing.updated) timing.updated = date;
      if (event.operation === "add_node") timing.created = date;
      const phase = phases[event.operation];
      if (phase && phase === timing.status) timing.phaseStarted = date;
    } else if (event.operation === "cancel_goal") {
      for (const [id, node] of Object.entries(goal.nodes)) {
        if (node.status !== "cancelled") continue;
        const cancelled = timings.get(id);
        cancelled.finished = date;
        if (!cancelled.updated || date > cancelled.updated) cancelled.updated = date;
      }
    }
  }
  return timings;
}

export function durationRanges(timing) {
  const terminal = ["completed", "failed", "cancelled"].includes(timing.status);
  const total = timing.started && (!terminal || timing.finished)
    ? {start: timing.started, end: terminal ? timing.finished : null} : null;
  let phase = null;
  if (timing.status === "running") phase = timing.phaseStarted || timing.started;
  if (timing.status === "waiting") phase = timing.waiting || timing.phaseStarted;
  if (timing.status === "dispatching") phase = timing.assigned || timing.phaseStarted;
  return {total, phase: phase ? {start: phase, end: null} : null};
}
