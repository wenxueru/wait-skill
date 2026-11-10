import {sessionUrl} from "../models/links.mjs";

export function createSessionPicker(scope, t, getView) {
  const $ = id => document.getElementById(id);
  const home = scope.get("home") || scope.get("session");
  const selected = scope.get("all") === "1" ? null : scope.get("session") || "";
  let sessions = [];

  function link(label, session, counts) {
    const item = document.createElement("a");
    const query = new URLSearchParams(scope);
    const {mode, view, tasks} = getView();
    query.set("mode", mode);
    query.set("view", view);
    query.set("tasks", tasks);
    item.href = sessionUrl(query, session);
    if (session === selected) item.setAttribute("aria-current", "page");
    const name = document.createElement("span");
    name.textContent = label;
    item.append(name);
    if (session) item.title = session;
    if (counts) {
      const summary = document.createElement("small");
      summary.textContent = t("scope.counts", counts);
      item.append(summary);
    }
    return item;
  }

  function render() {
    $("scopeLabel").textContent = selected === null ? t("scope.all")
      : selected === home ? t("scope.current") : selected || t("scope.select");
    $("sessionToggle").title = selected || t("scope.all");
  }

  function renderOptions() {
    const query = $("sessionSearch").value.trim().toLowerCase();
    const links = [];
    if (home) links.push(link(t("scope.current"), home, sessions.find(session => session.id === home)));
    links.push(link(t("scope.all"), null));
    for (const session of sessions) {
      if (session.id !== home && session.id.toLowerCase().includes(query)) links.push(link(session.id, session.id, session));
    }
    $("sessionOptions").replaceChildren(...links);
  }

  $("sessionSearch").oninput = renderOptions;
  $("sessionPanel").addEventListener("toggle", async event => {
    if (event.newState !== "open") return;
    $("sessionError").hidden = true;
    $("sessionSearch").value = "";
    renderOptions();
    try {
      const response = await fetch("/api/sessions");
      if (!response.ok) throw Error(t("scope.loadError"));
      sessions = await response.json();
      renderOptions();
    } catch (error) {
      $("sessionError").textContent = error.message;
      $("sessionError").hidden = false;
    }
  });
  return {render};
}
