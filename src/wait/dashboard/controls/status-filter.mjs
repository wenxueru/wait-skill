export function createStatusFilter(button, panel, t, onChange) {
  const selected = new Set();
  const choices = new Map();
  for (const state of ["all", "ready", "running", "waiting", "blocked", "pending", "completed", "failed", "cancelled"]) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = state;
    const text = document.createElement("span");
    label.append(input, text);
    panel.append(label);
    choices.set(state, {label, input, text});
    input.onchange = () => {
      if (state === "all") selected.clear();
      else if (input.checked) selected.add(state);
      else selected.delete(state);
      onChange();
    };
  }
  panel.addEventListener("toggle", event => {
    button.setAttribute("aria-expanded", event.newState === "open");
    if (event.newState !== "open") return;
    const rect = button.getBoundingClientRect();
    panel.style.left = Math.max(12, Math.min(rect.left, innerWidth - panel.offsetWidth - 12)) + "px";
    panel.style.top = Math.max(12, rect.bottom + panel.offsetHeight + 8 <= innerHeight
      ? rect.bottom + 8 : rect.top - panel.offsetHeight - 8) + "px";
  });
  return {
    get selected() { return [...selected]; },
    clear() { selected.clear(); },
    toggle(state) {
      if (selected.has(state)) selected.delete(state); else selected.add(state);
      onChange();
    },
    render(counts, describe) {
      for (const [state, {label, input, text}] of choices) {
        input.checked = state === "all" ? selected.size === 0 : selected.has(state);
        text.textContent = state === "all" ? t("workspace.all") : `${t(`state.${state}`)} · ${counts[state] || 0}`;
        if (state !== "all") {
          const meaning = describe(state);
          label.title = meaning;
          input.setAttribute("aria-description", meaning);
        }
      }
      button.dataset.active = selected.size > 0;
      const count = button.querySelector("span");
      count.textContent = selected.size;
      count.hidden = selected.size === 0;
      button.setAttribute("aria-label", t("workspace.filter") + ": " +
        (selected.size ? [...selected].map(state => t(`state.${state}`)).join(", ") : t("workspace.all")));
    }
  };
}
