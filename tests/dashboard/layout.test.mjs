import assert from "node:assert/strict";
import test from "node:test";
import {layoutGraph} from "../../src/wait/dashboard/graph/layout.mjs";

const nodesFrom = dependencies => Object.fromEntries(
  Object.entries(dependencies).map(([id, depends_on]) => [id, {depends_on}])
);

function segments(edge) {
  return edge.points.slice(1).map((point, index) => [edge.points[index], point]);
}

function crosses([a, b], [c, d]) {
  const side = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  return side(a, b, c) * side(a, b, d) < -1e-6 && side(c, d, a) * side(c, d, b) < -1e-6;
}

function entersCard([start, end], card) {
  let entry = 0, exit = 1;
  for (const [axis, size] of [["x", 280], ["y", 114]]) {
    const low = card[axis] + .01, high = card[axis] + size - .01;
    const delta = end[axis] - start[axis];
    if (!delta) {
      if (start[axis] <= low || start[axis] >= high) return false;
    } else {
      const bounds = [(low - start[axis]) / delta, (high - start[axis]) / delta].sort((a, b) => a - b);
      entry = Math.max(entry, bounds[0]);
      exit = Math.min(exit, bounds[1]);
    }
  }
  return entry < exit;
}

function checkGeometry(graph, nodes) {
  assert.deepEqual(Object.keys(graph.positions).sort(), Object.keys(nodes).sort());
  assert.equal(graph.edges.length, Object.values(nodes).reduce((sum, node) => sum + node.depends_on.length, 0));
  for (const [id, position] of Object.entries(graph.positions)) {
    assert.ok(position.x >= 0 && position.y >= 0);
    assert.ok(position.x + 280 <= graph.width && position.y + 114 <= graph.height);
    for (const dependency of nodes[id].depends_on) {
      assert.ok(graph.positions[dependency].x + 280 < position.x, `${dependency} must precede ${id}`);
    }
    for (const [otherId, other] of Object.entries(graph.positions)) {
      if (otherId <= id) continue;
      assert.ok(position.x + 280 <= other.x || other.x + 280 <= position.x ||
        position.y + 114 <= other.y || other.y + 114 <= position.y, `${id} overlaps ${otherId}`);
    }
  }
  for (const edge of graph.edges) {
    assert.ok(nodes[edge.to].depends_on.includes(edge.from));
    for (const point of edge.points) {
      assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
      assert.ok(point.x >= 0 && point.x <= graph.width && point.y >= 0 && point.y <= graph.height);
    }
    for (const [id, position] of Object.entries(graph.positions)) {
      if (id === edge.from || id === edge.to) continue;
      assert.ok(!segments(edge).some(segment => entersCard(segment, position)), `${edge.from} → ${edge.to} crosses ${id}`);
    }
  }
}

test("demo DAG routes long dependencies without crossings or card collisions", () => {
  const nodes = nodesFrom({
    prepare: [], train: ["prepare"], metrics: ["prepare"], checks: ["prepare"], docs: ["prepare"],
    export: ["train", "checks"], evaluate: ["export"], review: ["evaluate", "metrics"], release: ["review", "docs"]
  });
  const graph = layoutGraph(nodes);
  checkGeometry(graph, nodes);
  for (let i = 0; i < graph.edges.length; i++) {
    for (const other of graph.edges.slice(i + 1)) {
      assert.ok(!segments(graph.edges[i]).some(a => segments(other).some(b => crosses(a, b))));
    }
  }
  const reordered = Object.fromEntries(Object.entries(nodes).reverse().map(([id, node]) =>
    [id, {...node, status: "completed", depends_on: [...node.depends_on].reverse()}]));
  assert.deepEqual(layoutGraph(reordered), graph, "status and input order must not move the graph");
});

test("38-node fan-out and fan-in preserves every dependency and avoids task cards", () => {
  const dependencies = {prepare: []};
  for (let i = 0; i < 8; i++) {
    dependencies[`train-${i}`] = ["prepare"];
    dependencies[`export-${i}`] = [`train-${i}`];
    dependencies[`eval-${i}`] = [`export-${i}`];
    dependencies[`cleanup-${i}`] = [`eval-${i}`];
  }
  for (let i = 0; i < 4; i++) dependencies[`report-${i}`] = [`eval-${i * 2}`, `eval-${i * 2 + 1}`];
  dependencies.finish = ["report-0", "report-1", "report-2", "report-3"];
  const nodes = nodesFrom(dependencies);
  checkGeometry(layoutGraph(nodes), nodes);
});

test("empty and disconnected graphs have finite bounds and separate cards", () => {
  assert.deepEqual(layoutGraph({}), {width: 340, height: 240, positions: {}, edges: []});
  const nodes = nodesFrom({a: [], b: [], c: ["a"], d: ["b"], isolated: []});
  checkGeometry(layoutGraph(nodes), nodes);
});
