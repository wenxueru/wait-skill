import assert from "node:assert/strict";
import test from "node:test";
import {findTask, sessionUrl, taskUrl} from "../../src/wait/dashboard/models/links.mjs";

test("session switching preserves the original session and view, clearing stale task focus", () => {
  const initial = new URLSearchParams("session=first&mode=goal&view=graph&tasks=history&focus=/first.json&node=work");
  const switched = new URLSearchParams(sessionUrl(initial, "second").slice(2));
  assert.equal(switched.get("session"), "second");
  assert.equal(switched.get("home"), "first");
  assert.equal(switched.get("mode"), "goal");
  assert.equal(switched.get("view"), "graph");
  assert.equal(switched.get("tasks"), "history");
  assert.equal(switched.has("focus"), false);
  assert.equal(switched.has("node"), false);
  const all = new URLSearchParams(sessionUrl(switched, null).slice(2));
  assert.equal(all.get("all"), "1");
  assert.equal(all.get("home"), "first");
  const returned = new URLSearchParams(sessionUrl(all, all.get("home")).slice(2));
  assert.equal(returned.has("all"), false);
  assert.equal(returned.get("session"), "first");
  assert.equal(initial.get("focus"), "/first.json");
  assert.equal(new URLSearchParams(sessionUrl(new URLSearchParams(), null).slice(2)).get("all"), "1");
});

test("task links resolve exact available targets and scope deep links to their owning session", () => {
  const goal = {path: "/work/release plan.json", thread: "target-session", status: "open", nodes: {
    checkpoint: {status: "completed"}, export: {status: "pending"}
  }};
  const loop = {path: "/work/loop.json", session: "target-session", status: "active"};
  const watch = {watch_id: "timer", session: "target-session", state: "cancelled"};
  const tasks = {goals: [goal], loops: [loop], watches: [], linked_watches: [watch]};
  const origin = {mode: "goal", path: goal.path, node: "checkpoint"};
  assert.equal(findTask(tasks, origin), goal);
  assert.equal(findTask(tasks, {...origin, node: "deleted"}), undefined);
  assert.equal(findTask(tasks, {mode: "loop", path: "/missing.json"}), undefined);
  assert.equal(findTask({...tasks, loops: [{...loop, error: "Unreadable"}]}, {mode: "loop", path: loop.path}), undefined);
  const scope = new URLSearchParams("all=1&session=original&mode=loop&focus=stale&node=stale&view=graph");
  const target = new URLSearchParams(taskUrl(scope, origin, goal).slice(2));
  assert.equal(target.get("session"), "target-session");
  assert.equal(target.get("home"), "original");
  assert.equal(target.has("all"), false);
  assert.equal(target.get("focus"), goal.path);
  assert.equal(target.get("node"), "checkpoint");
  assert.equal(target.get("tasks"), "history");
  assert.equal(target.get("view"), "list");
  assert.equal(scope.get("node"), "stale");
  const timerOrigin = {mode: "wait", watch_id: watch.watch_id};
  assert.equal(findTask(tasks, timerOrigin), watch);
  const timerLink = new URLSearchParams(taskUrl(scope, timerOrigin, watch).slice(2));
  assert.equal(timerLink.get("mode"), "wait");
  assert.equal(timerLink.get("focus"), "timer");
  assert.equal(timerLink.get("tasks"), "history");
  assert.equal(timerLink.has("node"), false);
});
