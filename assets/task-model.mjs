export function nodeState(goal, node) {
  if (node.status === "dispatching") return "running";
  if (node.status !== "pending") return node.status;
  if (node.depends_on.some(id => goal.nodes[id].status !== "completed")) return "blocked";
  return goal.status === "open" ? "ready" : "pending";
}

export function nodeRows(goal) {
  const priority = ["failed", "running", "ready", "waiting", "blocked", "pending", "completed", "cancelled"];
  return Object.entries(goal.nodes)
    .map(([id, node]) => ({id, node, state: nodeState(goal, node)}))
    .sort((a, b) => priority.indexOf(a.state) - priority.indexOf(b.state));
}

export function timeDate(value) {
  if (value == null) return null;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function nodeTimings(goal) {
  const timings = new Map();
  for (const [id, node] of Object.entries(goal.nodes)) {
    const timing = {updated: null};
    for (const [key, value] of [
      ["started", node.started_at], ["waiting", node.wait?.since],
      ["finished", node.completed_at || node.failed_at], ["received", node.last_event?.at],
      ["assigned", node.assignment?.assigned_at || node.assignment?.prepared_at]
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
