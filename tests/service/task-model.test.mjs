import assert from "node:assert/strict";
import test from "node:test";
import {nodeRows, nodeTimings, timeDate} from "../../assets/task-model.mjs";

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
