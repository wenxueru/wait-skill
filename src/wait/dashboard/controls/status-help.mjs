export function createStatusHelp(tooltip) {
  let helpTrigger = null;

  function hide() {
    if (tooltip.matches(":popover-open")) tooltip.hidePopover();
    helpTrigger?.removeAttribute("aria-describedby");
    helpTrigger = null;
  }

  function show(trigger) {
    hide();
    helpTrigger = trigger;
    tooltip.textContent = trigger.dataset.statusHelp;
    trigger.setAttribute("aria-describedby", tooltip.id);
    tooltip.showPopover();
    const bounds = trigger.getBoundingClientRect();
    tooltip.style.left = Math.max(12, Math.min(bounds.left, innerWidth - tooltip.offsetWidth - 12)) + "px";
    const below = bounds.bottom + 8;
    tooltip.style.top = Math.max(12, below + tooltip.offsetHeight <= innerHeight - 12 ? below : bounds.top - tooltip.offsetHeight - 8) + "px";
  }

  document.addEventListener("pointerover", event => {
    const trigger = event.target.closest("[data-status-help]");
    if (trigger && !trigger.contains(event.relatedTarget)) show(trigger);
  });
  document.addEventListener("pointerout", event => {
    if (!helpTrigger || !helpTrigger.contains(event.target)) return;
    if (!helpTrigger.contains(event.relatedTarget) && !tooltip.contains(event.relatedTarget)) hide();
  });
  tooltip.onpointerleave = hide;
  document.addEventListener("focusin", event => {
    if (event.target.matches("[data-status-help]")) show(event.target);
  });
  document.addEventListener("focusout", event => {
    if (event.target === helpTrigger) hide();
  });
  document.addEventListener("click", event => {
    const trigger = event.target.closest("[data-status-help]");
    if (trigger && trigger.tagName !== "BUTTON") {
      event.preventDefault();
      event.stopPropagation();
      show(trigger);
    } else if (!tooltip.contains(event.target)) hide();
  }, true);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") hide();
    if (["Enter", " "].includes(event.key) && event.target.matches("[data-status-help]:not(button)")) {
      event.preventDefault();
      event.stopPropagation();
      show(event.target);
    }
  }, true);

  return {
    hide,
    attach(element, message) {
      element.dataset.statusHelp = message;
      element.setAttribute("aria-description", message);
    }
  };
}
