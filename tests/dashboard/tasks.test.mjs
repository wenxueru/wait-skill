import assert from "node:assert/strict";
import test from "node:test";
import {durationRanges, matchesTaskScope, scopeForStatuses, nextSort, nodeRows, nodeTimings, orderRows, timeDate} from "../../src/wait/dashboard/models/tasks.mjs";

test("updated sorting toggles direction, keeps missing timestamps last and preserves ties", () => {
  const rows = ["missing", "newest", "oldest", "tied"].map(id => ({id}));
  const timings = new Map([
    ["missing", {updated: null}], ["newest", {updated: new Date(2000)}],
    ["oldest", {updated: new Date(0)}], ["tied", {updated: new Date(2000)}]
  ]);
  assert.deepEqual(orderRows(rows, timings, {key: "updated", direction: "desc"}).map(row => row.id), ["newest", "tied", "oldest", "missing"]);
  assert.deepEqual(orderRows(rows, timings, {key: "updated", direction: "asc"}).map(row => row.id), ["oldest", "newest", "tied", "missing"]);
  assert.equal(orderRows(rows, timings, null), rows);
  assert.equal(rows[0].id, "missing");
});

test("both columns cycle descending, ascending, default and switching columns starts descending", () => {
  for (const key of ["updated", "duration"]) {
    const descending = nextSort(null, key);
    assert.deepEqual(descending, {key, direction: "desc"});
    const ascending = nextSort(descending, key);
    assert.deepEqual(ascending, {key, direction: "asc"});
    assert.equal(nextSort(ascending, key), null);
  }
  assert.deepEqual(nextSort({key: "updated", direction: "asc"}, "duration"), {key: "duration", direction: "desc"});
});

test("duration sorting uses displayed phase time, handles zero and reorders live versus finished tasks", () => {
  const rows = ["running", "done", "waiting", "zero", "pending"].map(id => ({id}));
  const timings = new Map([
    ["running", {status: "running", started: timeDate(10)}],
    ["done", {status: "completed", started: timeDate(0), finished: timeDate(100)}],
    ["waiting", {status: "waiting", started: timeDate(0), waiting: timeDate(80)}],
    ["zero", {status: "completed", started: timeDate(0), finished: timeDate(0)}],
    ["pending", {status: "pending"}]
  ]);
  const sort = {key: "duration", direction: "asc"};
  assert.deepEqual(orderRows(rows, timings, sort, 100000).map(row => row.id), ["zero", "waiting", "running", "done", "pending"]);
  assert.deepEqual(orderRows(rows, timings, sort, 200000).map(row => row.id), ["zero", "done", "waiting", "running", "pending"]);
  assert.deepEqual(orderRows(rows, timings, {...sort, direction: "desc"}, 100000).map(row => row.id), ["done", "running", "waiting", "zero", "pending"]);
});

test("task priority reflects dependency readiness and user pause", () => {
  const goal = {status: "open", nodes: {
    done: {status: "completed", depends_on: []},
    blocked: {status: "pending", depends_on: ["ready"]},
    ready: {status: "pending", depends_on: ["done"]},
    waiting: {status: "waiting", depends_on: []},
    running: {status: "dispatching", depends_on: []},
    failed: {status: "failed", depends_on: []}
  }};
  assert.deepEqual(nodeRows(goal).map(({id, state}) => [id, state]), [
    ["failed", "failed"], ["running", "running"], ["ready", "ready"],
    ["waiting", "waiting"], ["blocked", "blocked"], ["done", "completed"]
  ]);
  goal.status = "paused";
  const paused = Object.fromEntries(nodeRows(goal).map(({id, state}) => [id, state]));
  assert.equal(paused.ready, "pending");
  assert.equal(paused.blocked, "blocked");
});

test("phase duration restarts on wake while total elapsed retains the original start", () => {
  const goal = {nodes: {
    work: {status: "running", started_at: 10},
    wait: {status: "waiting", started_at: 10, wait: {since: 20}},
    done: {status: "completed", started_at: 10, completed_at: 50},
    cancelled: {status: "cancelled", started_at: 10},
    pending: {status: "pending"}
  }, events: [
    {operation: "start_node", node_id: "work", at: 10},
    {operation: "activate_wait", node_id: "work", at: 20},
    {operation: "wake_node", node_id: "work", at: 40},
    {operation: "edit_node", node_id: "work", at: 45},
    {operation: "cancel_goal", at: 60}
  ]};
  const times = nodeTimings(goal);
  const running = durationRanges(times.get("work"));
  assert.equal(+running.total.start, 10000);
  assert.equal(+running.phase.start, 40000);
  assert.equal(running.phase.end, null);
  assert.equal(+durationRanges(times.get("wait")).phase.start, 20000);
  const done = durationRanges(times.get("done"));
  assert.equal(done.total.end - done.total.start, 40000);
  assert.equal(done.phase, null);
  assert.equal(+durationRanges(times.get("cancelled")).total.end, 60000);
  assert.deepEqual(durationRanges(times.get("pending")), {total: null, phase: null});
});

test("timestamps use node records, retain lifecycle times and ignore unrelated goal events", () => {
  const goal = {nodes: {
    running: {started_at: 10, assignment: {assigned_at: 15}},
    waiting: {started_at: 10, wait: {since: 20}, last_event: {at: 25}},
    done: {status: "completed", completed_at: 30},
    failed: {status: "failed", failed_at: 35},
    cancelled: {status: "cancelled"},
    unknown: {},
    invalid: {started_at: "invalid"}
  }, events: [
    {operation: "add_node", node_id: "running", at: 0},
    {operation: "edit_node", node_id: "running", at: "1970-01-01T00:00:18.000Z"},
    {operation: "cancel_goal", at: 40},
    {operation: "verify_goal", at: 50}
  ]};
  const times = nodeTimings(goal);
  assert.equal(+times.get("running").created, 0);
  assert.equal(+times.get("running").started, 10000);
  assert.equal(+times.get("running").updated, 18000);
  assert.equal(+times.get("waiting").waiting, 20000);
  assert.equal(+times.get("waiting").updated, 25000);
  assert.equal(+times.get("done").finished, 30000);
  assert.equal(+times.get("done").updated, 30000);
  assert.equal(+times.get("failed").finished, 35000);
  assert.equal(+times.get("cancelled").finished, 40000);
  assert.equal(times.get("unknown").updated, null);
  assert.equal(times.get("invalid").updated, null);
  assert.equal(timeDate(undefined), null);
});

test("current tasks hide completed and cancelled history while keeping failures visible", () => {
  const states = ["ready", "running", "waiting", "blocked", "pending", "failed", "completed", "cancelled"];
  assert.deepEqual(states.filter(state => matchesTaskScope(state, "current")), states.slice(0, 6));
  assert.deepEqual(states.filter(state => matchesTaskScope(state, "history")), ["completed", "cancelled"]);
  assert.deepEqual(states.filter(state => matchesTaskScope(state, "all")), states);
});

test("status selections widen task scope so mixed current and historical states remain visible", () => {
  assert.equal(scopeForStatuses(["running", "failed"], "current"), "current");
  assert.equal(scopeForStatuses(["completed", "cancelled"], "current"), "history");
  assert.equal(scopeForStatuses(["running", "completed"], "current"), "all");
  assert.equal(scopeForStatuses(["failed"], "history"), "current");
  assert.equal(scopeForStatuses(["completed"], "all"), "all");
  assert.equal(scopeForStatuses([], "current"), "current");
  assert.equal(scopeForStatuses([], "history"), "history");
});
