import {Graph, layout} from "./vendor/dagre.mjs";

export function layoutGraph(nodes) {
  const graph = new Graph()
    .setGraph({rankdir: "LR", nodesep: 30, edgesep: 18, ranksep: 64, marginx: 28, marginy: 28})
    .setDefaultEdgeLabel(() => ({}));
  const ids = Object.keys(nodes).sort();
  for (const id of ids) graph.setNode(id, {width: 280, height: 114});
  for (const id of ids) {
    for (const dependency of [...nodes[id].depends_on].sort()) graph.setEdge(dependency, id);
  }
  layout(graph);
  return {
    width: Math.max(340, graph.graph().width),
    height: Math.max(240, graph.graph().height),
    positions: Object.fromEntries(ids.map(id => {
      const {x, y, width, height} = graph.node(id);
      return [id, {x: x - width / 2, y: y - height / 2}];
    })),
    edges: graph.edges().map(edge => {
      const from = graph.node(edge.v), to = graph.node(edge.w);
      const route = graph.edge(edge).points.slice(1, -1);
      // Keep card connections on the right/left sides; turns stay in the inter-column gaps.
      return {from: edge.v, to: edge.w, points: [
        {x: from.x + from.width / 2, y: from.y}, {x: route[0].x, y: from.y},
        ...route,
        {x: route.at(-1).x, y: to.y}, {x: to.x - to.width / 2, y: to.y}
      ]};
    })
  };
}
