import {matchesTaskScope} from "./tasks.mjs";

export function sessionUrl(scope, session) {
  const query = new URLSearchParams(scope);
  const home = scope.get("home") || scope.get("session");
  if (home) query.set("home", home);
  if (session === null) query.set("all", "1");
  else {
    query.delete("all");
    query.set("session", session);
  }
  query.delete("focus");
  query.delete("node");
  return "/?" + query.toString();
}

export function findTask(tasks, origin) {
  let task;
  if (origin.mode === "goal") {
    task = tasks.goals.find(task => task.path === origin.path);
    if (origin.node && !task?.nodes?.[origin.node]) return;
  } else if (origin.mode === "loop") {
    task = tasks.loops.find(task => task.path === origin.path);
  } else {
    task = tasks.watches.find(task => task.watch_id === origin.watch_id) ||
      tasks.linked_watches.find(task => task.watch_id === origin.watch_id);
  }
  if (task && !task.error) return task;
}

export function taskUrl(scope, origin, task) {
  const query = new URLSearchParams(scope);
  const session = origin.mode === "goal" ? task.thread : task.session;
  if (session) { query.set("session", session); query.delete("all"); }
  const home = scope.get("home") || scope.get("session");
  if (home) query.set("home", home);
  query.set("mode", origin.mode);
  query.set("view", "list");
  query.set("focus", origin.watch_id || origin.path);
  if (origin.mode === "goal" && origin.node) query.set("node", origin.node); else query.delete("node");
  let state = task.state || task.status;
  if (origin.mode === "goal" && origin.node) state = task.nodes[origin.node].status;
  query.set("tasks", matchesTaskScope(state, "history") ? "history" : "current");
  return "/?" + query.toString();
}
