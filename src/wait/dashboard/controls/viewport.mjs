import {select, zoom, zoomIdentity, zoomTransform} from "../../../../assets/vendor/ui.mjs";

export function createGraphViewport(graph, controls, dismissOverlays) {
  const surface = select(graph);
  const level = controls.querySelector("#zoomLevel");
  const range = controls.querySelector("#zoomRange");
  let svg;
  let fitNextRender = true;

  function update(transform) {
    const {x, y, k} = transform;
    const bounds = svg.viewBox.baseVal;
    svg.style.width = bounds.width * k + "px";
    svg.style.height = bounds.height * k + "px";
    svg.style.transform = `translate(${x}px, ${y}px)`;
    level.textContent = Math.round(k * 100) + "%";
    range.value = k * 100;
  }

  const behavior = zoom()
    .scaleExtent([.01, 4])
    .clickDistance(5)
    .filter(event => svg && (event.type === "wheel"
      ? event.ctrlKey || event.metaKey
      : event.type !== "dblclick" && !event.ctrlKey && !event.button))
    .wheelDelta(event => -event.deltaY * .005 / Math.LN2)
    .on("zoom", ({transform}) => {
      dismissOverlays();
      update(transform);
    })
    .on("start", ({sourceEvent}) => {
      if (sourceEvent?.type !== "mousedown") return;
      graph.classList.add("panning");
    })
    .on("end", () => graph.classList.remove("panning"));
  surface.call(behavior).on("dblclick.zoom", null);

  function fitGraph() {
    if (!svg) return;
    const bounds = svg.viewBox.baseVal;
    const k = Math.max(.01, Math.min(1, (graph.clientWidth - 16) / bounds.width, (graph.clientHeight - 16) / bounds.height));
    const x = (graph.clientWidth - bounds.width * k) / 2;
    const y = (graph.clientHeight - bounds.height * k) / 2;
    surface.call(behavior.transform, zoomIdentity.translate(x, y).scale(k));
  }

  function scaleBy(factor) {
    if (svg) surface.call(behavior.scaleBy, factor);
  }

  function scaleTo(value) {
    if (svg) surface.call(behavior.scaleTo, value);
  }

  controls.querySelector("#zoomIn").onclick = () => scaleBy(1.2);
  controls.querySelector("#zoomOut").onclick = () => scaleBy(1 / 1.2);
  controls.querySelector("#zoomReset").onclick = () => scaleTo(1);
  range.oninput = event => scaleTo(Number(event.target.value) / 100);
  controls.querySelector("#fitGraph").onclick = fitGraph;
  graph.addEventListener("wheel", event => {
    if (!svg || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const {x, y, k} = zoomTransform(graph);
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? graph.clientHeight : 1;
    surface.call(behavior.transform, zoomIdentity.translate(x - event.deltaX * unit, y - event.deltaY * unit).scale(k));
  }, {passive: false});
  graph.onkeydown = event => {
    if (!["+", "=", "-", "0", "f"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "f") fitGraph();
    else if (event.key === "0") scaleTo(1);
    else scaleBy(event.key === "-" ? 1 / 1.2 : 1.2);
  };

  return {
    reset() { fitNextRender = true; },
    render(content) {
      svg = content;
      graph.replaceChildren(svg);
      if (fitNextRender) {
        fitNextRender = false;
        fitGraph();
      } else update(zoomTransform(graph));
    }
  };
}
