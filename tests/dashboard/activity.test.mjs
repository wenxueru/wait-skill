import assert from "node:assert/strict";
import test from "node:test";
import {activityId, activityWorkspace, collapseLoopTimers, relationNodes, watchState} from "../../src/wait/dashboard/models/activity.mjs";
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
  const workspace = activityWorkspace(tasks, "wait");
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
  const workspace = activityWorkspace(tasks, "loop");
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
  const cancelled = nodeTimings(activityWorkspace(tasks, "loop")).get(parent);
  assert.equal(+durationRanges(cancelled).total.end, 40000);
});

test("relationship filtering retains ancestors and a selected Loop's current timer, excluding other history", () => {
  const workspace = activityWorkspace(fixture(), "wait");
  assert.deepEqual(Object.keys(relationNodes(workspace.nodes, new Set([activityId("wait", "independent")]))), [activityId("wait", "independent")]);
  const linked = relationNodes(workspace.nodes, new Set([activityId("wait", "timer")]));
  assert.equal(Object.keys(linked).length, 4);
  assert.equal(Object.values(linked).some(node => node.shortId === "direct"), false);
  const loop = activityWorkspace(fixture(), "loop");
  assert.equal(Object.keys(relationNodes(loop.nodes, new Set([activityId("loop", "/loop.json")]))).length, 4);
  assert.deepEqual(relationNodes(loop.nodes, new Set()), {});
});

test("unavailable parent references stay explicit and never infer other goal dependencies", () => {
  const tasks = fixture();
  tasks.goals = [];
  tasks.loops = [];
  const workspace = activityWorkspace(tasks, "wait");
  assert.equal(nodeRows(workspace).length, 3);
  assert.deepEqual(workspace.nodes[activityId("goal", "/g.json", "deploy")].depends_on, []);
  assert.equal(Object.values(workspace.nodes).some(node => node.shortId === "prepare"), false);
  assert.doesNotThrow(() => layoutGraph(workspace.nodes));
});

test("watcher state separates a live query from event delivery; terminal durations stop", () => {
  assert.equal(watchState({state: "active", phase: "querying"}), "running");
  assert.equal(watchState({state: "active", phase: "running", result: {event: "exited"}}), "waiting");
  assert.equal(watchState({state: "failed", phase: "running"}), "failed");
  const workspace = activityWorkspace(fixture(), "wait");
  const timing = nodeTimings(workspace).get(activityId("wait", "direct"));
  assert.equal(durationRanges(timing).total.end - durationRanges(timing).total.start, 30000);
  assert.equal(durationRanges(timing).phase, null);
});

test("empty and unreadable records stay inspectable without fabricating start times", () => {
  const tasks = {goals: [], loops: [{path: "/broken.json", error: "Unreadable state"}], watches: [{path: "bad-watch", error: "Unreadable watcher"}], linked_watches: []};
  for (const mode of ["wait", "loop"]) {
    const workspace = activityWorkspace(tasks, mode);
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
  const workspace = activityWorkspace(tasks, "wait");
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
  const loopView = collapseLoopTimers(activityWorkspace(tasks, "loop").nodes);
  assert.equal(Object.keys(loopView.nodes).length, 3);
  assert.equal(loopView.nodes[loopId].timerCount, 1);
});
