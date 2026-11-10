export function createGraphViewport(graph, controls, dismissOverlays) {
  let graphZoom = null;
  let graphOffset = {x: 0, y: 0};
  function applyZoom() {
    const svg = graph.querySelector("svg");
    if (!svg) return;
    const bounds = svg.viewBox.baseVal;
    svg.style.width = bounds.width * graphZoom + "px";
    svg.style.height = bounds.height * graphZoom + "px";
    svg.style.transform = `translate(${graphOffset.x}px, ${graphOffset.y}px)`;
    controls.querySelector("#zoomLevel").textContent = Math.round(graphZoom * 100) + "%";
    controls.querySelector("#zoomRange").value = graphZoom * 100;
  }

  function setZoom(value, x = graph.clientWidth / 2, y = graph.clientHeight / 2) {
    const before = graphZoom;
    const worldX = (x - graphOffset.x) / before;
    const worldY = (y - graphOffset.y) / before;
    graphZoom = Math.min(4, Math.max(.01, value));
    graphOffset = {x: x - worldX * graphZoom, y: y - worldY * graphZoom};
    applyZoom();
  }

  function fitGraph() {
    const bounds = graph.querySelector("svg").viewBox.baseVal;
    graphZoom = Math.max(.01, Math.min(1, (graph.clientWidth - 16) / bounds.width, (graph.clientHeight - 16) / bounds.height));
    graphOffset = {x: (graph.clientWidth - bounds.width * graphZoom) / 2, y: (graph.clientHeight - bounds.height * graphZoom) / 2};
    applyZoom();
  }

  controls.querySelector("#zoomIn").onclick = () => setZoom(graphZoom * 1.2);
  controls.querySelector("#zoomOut").onclick = () => setZoom(graphZoom / 1.2);
  controls.querySelector("#zoomReset").onclick = () => setZoom(1);
  controls.querySelector("#zoomRange").oninput = event => setZoom(Number(event.target.value) / 100);
  controls.querySelector("#fitGraph").onclick = fitGraph;
  graph.addEventListener("wheel", event => {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      const bounds = graph.getBoundingClientRect();
      setZoom(graphZoom * Math.exp(-event.deltaY * .005), event.clientX - bounds.left, event.clientY - bounds.top);
    } else {
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? graph.clientHeight : 1;
      graphOffset.x -= event.deltaX * unit;
      graphOffset.y -= event.deltaY * unit;
      applyZoom();
    }
  }, {passive: false});
  graph.onkeydown = event => {
    if (["+", "=", "-", "0", "f"].includes(event.key)) {
      event.preventDefault();
      if (event.key === "f") fitGraph();
      else setZoom(event.key === "0" ? 1 : graphZoom * (event.key === "-" ? 1 / 1.2 : 1.2));
    }
  };
  let pan = null;
  let suppressGraphClick = false;
  graph.onpointerdown = event => {
    if (!event.isPrimary || event.button !== 0) return;
    suppressGraphClick = false;
    pan = {id: event.pointerId, x: event.clientX, y: event.clientY, offset: {...graphOffset}, dragging: false};
  };
  graph.onpointermove = event => {
    if (!pan || pan.id !== event.pointerId) return;
    const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
    if (!pan.dragging && Math.hypot(dx, dy) < 5) return;
    if (!pan.dragging) {
      pan.dragging = true;
      dismissOverlays();
      graph.setPointerCapture(event.pointerId);
      graph.classList.add("panning");
      graph.focus({preventScroll: true});
    }
    event.preventDefault();
    graphOffset = {x: pan.offset.x + dx, y: pan.offset.y + dy};
    applyZoom();
  };
  function finishPan(event) {
    if (!pan || pan.id !== event.pointerId) return;
    suppressGraphClick = event.type === "pointerup" && pan.dragging;
    pan = null;
    graph.classList.remove("panning");
    if (graph.hasPointerCapture(event.pointerId)) graph.releasePointerCapture(event.pointerId);
  }
  graph.onpointerup = graph.onpointercancel = graph.onlostpointercapture = finishPan;
  graph.onpointerleave = () => {
    if (pan && !pan.dragging) pan = null;
  };
  graph.addEventListener("click", event => {
    if (suppressGraphClick && event.detail > 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      dismissOverlays();
      suppressGraphClick = false;
    }
  }, true);

  return {
    reset() {
      graphZoom = null;
      graphOffset = {x: 0, y: 0};
    },
    render(svg) {
      graph.replaceChildren(svg);
      if (graphZoom === null) fitGraph(); else applyZoom();
    }
  };
}
