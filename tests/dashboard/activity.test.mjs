import assert from "node:assert/strict";
import test from "node:test";
import {activityId, collapseLoopTimers, relationNodes, sessionActivity, wakeStatus, watchState} from "../../src/wait/dashboard/models/activity.mjs";
import {durationRanges, nodeRows, nodeTimings} from "../../src/wait/dashboard/models/tasks.mjs";
import {layoutGraph} from "../../src/wait/dashboard/graph/layout.mjs";

function fixture() {
  return {goals: [{path: "/g.json", status: "open", objective: "Release", nodes: {
    prepare: {title: "Prepare", kind: "local", status: "completed", depends_on: []},
    deploy: {title: "Deploy", kind: "external", status: "running", depends_on: ["prepare"], started_at: 10}
  }, events: [{node_id: "deploy", operation: "add_node", at: 0}, {node_id: "deploy", operation: "wake_node", at: 30}]}], loops: [{path: "/loop.json", task: "Inspect deployment", status: "active", phase: "waiting",
    watch_id: "timer", goal: {state: "/g.json", node: "deploy"},
    events: [{at: 0, operation: "init"}, {at: 20, operation: "schedule"}]}],
    watches: [{watch_id: "independent", label: "Independent condition", state: "active", phase: "querying", submitted_at: 0, updated_at: 5}],
    linked_watches: [{watch_id: "timer", label: "Timer", state: "active", phase: "running", submitted_at: 20, updated_at: 20,
      owner: {mode: "loop", path: "/loop.json"}},
    {watch_id: "direct", label: "External deployment", state: "completed", submitted_at: 10, updated_at: 40,
      owner: {mode: "goal", path: "/g.json", node: "deploy"}}]};
}

test("Wait rows include every watcher once and only recorded relationships enter the graph", () => {
  const tasks = fixture();
  const workspace = sessionActivity(tasks).workspace("wait");
  const rows = nodeRows(workspace);
  assert.equal(rows.length, 3);
  const independent = workspace.nodes[activityId("wait", "independent")];
  assert.deepEqual(independent.depends_on, []);
  assert.equal(independent.status, "running");
  assert.deepEqual(workspace.nodes[activityId("wait", "timer")].depends_on, [activityId("loop", "/loop.json")]);
  assert.deepEqual(workspace.nodes[activityId("loop", "/loop.json")].depends_on, [activityId("goal", "/g.json", "deploy")]);
  assert.deepEqual(workspace.nodes[activityId("goal", "/g.json", "deploy")].depends_on, [activityId("goal", "/g.json", "prepare")]);
  assert.equal(layoutGraph(workspace.nodes).edges.length, 4);
  assert.equal(tasks.linked_watches[0].depends_on, undefined);
});

test("Loop rows omit context timers; live phase duration differs from total lifetime", () => {
  const tasks = fixture();
  const workspace = sessionActivity(tasks).workspace("loop");
  assert.equal(nodeRows(workspace).length, 1);
  assert.equal(Object.keys(workspace.nodes).length, 4);
  const timing = nodeTimings(workspace).get(activityId("loop", "/loop.json"));
  const ranges = durationRanges(timing);
  assert.equal(+ranges.total.start, 0);
  assert.equal(+ranges.phase.start, 20000);
  assert.equal(workspace.nodes[activityId("wait", "timer")].context, true);
  const parent = activityId("goal", "/g.json", "deploy");
  const parentTiming = nodeTimings(workspace).get(parent);
  assert.equal(+parentTiming.created, 0);
  assert.equal(+durationRanges(parentTiming).total.start, 10000);
  assert.equal(+durationRanges(parentTiming).phase.start, 30000);
  tasks.goals[0].nodes.deploy.status = "cancelled";
  tasks.goals[0].events.push({operation: "cancel_goal", at: 40});
  const cancelled = nodeTimings(sessionActivity(tasks).workspace("loop")).get(parent);
  assert.equal(+durationRanges(cancelled).total.end, 40000);
});

test("the current Wait graph keeps nine live watches and relevant context out of 265 records", () => {
  const tasks = fixture();
  tasks.watches.push(
    ...Array.from({length: 7}, (_, index) => ({
      watch_id: "live-" + index, state: "active", phase: "querying"
    })),
    ...Array.from({length: 30}, (_, index) => ({
      watch_id: "failure-" + index, state: "failed"
    })),
    ...Array.from({length: 225}, (_, index) => ({
      watch_id: "history-" + index, state: index % 2 ? "completed" : "cancelled"
    }))
  );
  const activity = sessionActivity(tasks);
  const workspace = activity.workspace("wait");
  const rows = nodeRows(workspace);
  const current = rows.filter(({scope}) => scope === "current");
  assert.equal(rows.length, 265);
  assert.equal(current.length, 9);
  assert.equal(rows.filter(({scope}) => scope === "history").length, 226);
  assert.equal(activity.summary.attention.length, 30);

  const graph = relationNodes(workspace.nodes, new Set(current.map(({id}) => id)));
  assert.equal(graph[activityId("goal", "/g.json", "prepare")].status, "completed");
  assert.equal(graph[activityId("wait", "direct")], undefined);
  assert.equal(Object.keys(graph).length, 12);

  const loop = activity.workspace("loop");
  const related = relationNodes(loop.nodes, new Set([activityId("loop", "/loop.json")]));
  assert.ok(related[activityId("wait", "timer")]);
  assert.deepEqual(relationNodes(loop.nodes, new Set()), {});
});

test("recorded Goal and Loop lifecycle events retire older failures and keep the current wait", () => {
  const tasks = fixture();
  const goal = tasks.goals[0];
  goal.events = [
    {operation: "prepare_wait", node_id: "deploy", details: {watch_id: "superseded"}},
    {operation: "activate_wait", node_id: "deploy", details: {watch_id: "superseded"}},
    {operation: "prepare_wait", node_id: "deploy", details: {watch_id: "latest-failure"}},
    {operation: "activate_wait", node_id: "deploy", details: {watch_id: "latest-failure"}},
    {operation: "wake_node", node_id: "deploy", details: {event_id: "other-owner"}}
  ];
  goal.nodes.review = {title: "Review", kind: "external", status: "running", depends_on: []};
  const loop = tasks.loops[0];
  loop.events = [
    {operation: "schedule", watch_id: "previous-timer"},
    {operation: "begin", event_id: "previous-timer"},
    {operation: "schedule", watch_id: loop.watch_id}
  ];
  const goalOwner = {mode: "goal", path: goal.path, node: "deploy"};
  const loopOwner = {mode: "loop", path: loop.path};
  tasks.linked_watches.push(...[
    ["superseded", goalOwner], ["previous-timer", loopOwner], ["latest-failure", goalOwner],
    ["other-owner", {...goalOwner, node: "review"}]
  ].map(([watch_id, owner]) => ({
    watch_id, owner, state: "failed", result: {notification: "unconfirmed"}
  })));

  const activity = sessionActivity(tasks);
  const rows = nodeRows(activity.workspace("wait"));
  assert.deepEqual(rows.filter(({state, scope}) => state === "failed" && scope === "history")
    .map(({node}) => node.task.watch_id), ["superseded", "previous-timer"]);
  assert.deepEqual(activity.summary.attention.map(({task}) => task.watch_id), ["latest-failure", "other-owner"]);

  goal.events.push({operation: "wake_node", node_id: "deploy", details: {event_id: "latest-failure"}});
  const next = sessionActivity(tasks);
  const handled = nodeRows(next.workspace("wait")).find(({node}) => node.task.watch_id === "latest-failure");
  assert.equal(handled.scope, "history");
  assert.deepEqual(next.summary.attention.map(({task}) => task.watch_id), ["other-owner"]);
});

test("unavailable parent references stay explicit and never infer other goal dependencies", () => {
  const tasks = fixture();
  tasks.goals = [];
  tasks.loops = [];
  const workspace = sessionActivity(tasks).workspace("wait");
  assert.equal(nodeRows(workspace).length, 3);
  assert.deepEqual(workspace.nodes[activityId("goal", "/g.json", "deploy")].depends_on, []);
  assert.equal(Object.values(workspace.nodes).some(node => node.shortId === "prepare"), false);
  assert.doesNotThrow(() => layoutGraph(workspace.nodes));
});

test("watcher state separates a live query from event delivery; terminal durations stop", () => {
  assert.equal(watchState({state: "active", phase: "querying"}), "running");
  assert.equal(watchState({state: "active", phase: "running", result: {event: "exited"}}), "waiting");
  assert.equal(watchState({state: "failed", phase: "running"}), "failed");
  const workspace = sessionActivity(fixture()).workspace("wait");
  const timing = nodeTimings(workspace).get(activityId("wait", "direct"));
  assert.equal(durationRanges(timing).total.end - durationRanges(timing).total.start, 30000);
  assert.equal(durationRanges(timing).phase, null);
});

test("empty and unreadable records stay inspectable without fabricating start times", () => {
  const tasks = {goals: [], loops: [{path: "/broken.json", error: "Unreadable state"}], watches: [{path: "bad-watch", error: "Unreadable watcher"}], linked_watches: []};
  const activity = sessionActivity(tasks);
  for (const mode of ["wait", "loop"]) {
    const workspace = activity.workspace(mode);
    const [row] = nodeRows(workspace);
    assert.equal(row.state, "failed");
    assert.equal(durationRanges(nodeTimings(workspace).get(row.id)).total, null);
  }
});

test("loop timers collapse only in the graph, preserving ownership, raw records and filtered membership", () => {
  const tasks = fixture();
  for (let i = 0; i < 40; i++) tasks.linked_watches.push({
    watch_id: "past-" + i, label: "Loop timer", state: i === 0 ? "failed" : "completed",
    owner: {mode: "loop", path: "/loop.json"}
  });
  const activity = sessionActivity(tasks);
  const workspace = activity.workspace("wait");
  const grouped = collapseLoopTimers(workspace.nodes);
  const loopId = activityId("loop", "/loop.json");
  assert.equal(nodeRows(workspace).length, 43);
  assert.equal(Object.keys(grouped.nodes).length, 5);
  assert.equal(grouped.nodes[loopId].timerCount, 41);
  assert.equal(grouped.nodes[loopId].failedTimers, 1);
  assert.deepEqual(grouped.nodes[loopId].depends_on, [activityId("goal", "/g.json", "deploy")]);
  assert.ok(grouped.nodes[activityId("wait", "independent")]);
  assert.ok(grouped.nodes[activityId("wait", "direct")]);
  assert.equal(workspace.nodes[loopId].timerCount, undefined);
  assert.equal(grouped.members.get(activityId("wait", "past-3")), loopId);
  const subset = collapseLoopTimers(relationNodes(workspace.nodes, new Set([activityId("wait", "past-3")])));
  assert.equal(subset.nodes[loopId].timerCount, 1);
  assert.equal(Object.keys(subset.nodes).length, 3);
  assert.equal(layoutGraph(subset.nodes).edges.length, 2);
  const loopView = collapseLoopTimers(activity.workspace("loop").nodes);
  assert.equal(Object.keys(loopView.nodes).length, 3);
  assert.equal(loopView.nodes[loopId].timerCount, 1);
});

test("wake status distinguishes pending retries, accepted wakes and ambiguous delivery", () => {
  const retryAt = 1234;
  for (const [state, notification, expected] of [
    ["active", "notification_unavailable_retrying", {status: "pending", retryAt}],
    ["completed", "started", {status: "started"}],
    ["failed", "unconfirmed", {status: "review", hint: "unconfirmed"}],
    ["completed", "queued", {status: "review", hint: "queued"}],
    ["failed", "notification_unavailable", {status: "review", hint: "failed"}],
    ["active", undefined, null],
    ["completed", "disabled", null],
    ["cancelled", "busy_retrying", null]
  ]) {
    assert.deepEqual(wakeStatus({state, result: {notification, next_notification_at: retryAt}}), expected, notification);
  }
});

test("delivery state controls visibility and the session summary groups unresolved events", () => {
  const wake = (watch_id, state, notification) => ({watch_id, state, result: {notification}});
  const native = wake("native", "completed", "native_pending");
  const standalone = {goals: [], loops: [], linked_watches: [], watches: [native]};
  assert.equal(sessionActivity(standalone).summary.status, "waiting");

  const goals = [{
    path: "/g.json", status: "open", activity: "orphaned_wait",
    nodes: {upstream: {title: "Upstream", status: "failed"}, downstream: {title: "Downstream", status: "waiting"}}
  }];
  const tasks = {goals, loops: [], watches: [
    wake("offline-1", "active", "notification_unavailable_retrying"),
    wake("busy", "active", "busy_retrying"),
    wake("uncertain", "completed", "unconfirmed"),
    wake("native-failure", "failed", "native_pending"),
    {...wake("cancelled", "failed"), result: {event: "activation_cancelled"}},
    native,
    wake("legacy", "completed", "queued")
  ], linked_watches: [wake("offline-2", "active", "notification_unavailable_retrying")]};
  const activity = sessionActivity(tasks);
  const rows = nodeRows(activity.workspace("wait"));
  assert.deepEqual(Object.fromEntries(rows.map(({node, scope}) => [node.task.watch_id, scope])), {
    "offline-1": "current", busy: "current", uncertain: "attention",
    "native-failure": "attention", cancelled: "history", native: "current", legacy: "history", "offline-2": "current"
  });
  assert.equal(rows.find(row => row.node.task.watch_id === "cancelled").state, "cancelled");

  const {status, attention, offline, unavailable} = activity.summary;
  assert.equal(status, "attention");
  assert.equal(offline, 2);
  assert.equal(unavailable, false);
  assert.deepEqual(attention.map(({origin, reason}) => [origin, reason]), [
    [{mode: "wait", watch_id: "uncertain"}, "wake.hint.unconfirmed"],
    [{mode: "wait", watch_id: "native-failure"}, "attention.waitFailed"],
    [{mode: "goal", path: "/g.json", node: "upstream"}, "attention.goalFailed"],
    [{mode: "goal", path: "/g.json"}, "attention.missingWatcher"]
  ]);
});
