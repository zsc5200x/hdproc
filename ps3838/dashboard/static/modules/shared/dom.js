import { dom } from "../dom.js";
import { state } from "../state.js";

export function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

export function svgElement(tag, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, String(value));
  }
  return node;
}

export function setupSectionCollapse(panel, toggle, content, storageKey) {
  if (!panel || !toggle || !content) return;

  const applyCollapsed = (collapsed, persist = true) => {
    panel.classList.toggle("is-collapsed", collapsed);
    content.hidden = collapsed;
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.title = collapsed ? "展开" : "收起";
    if (persist) {
      try {
        window.localStorage.setItem(storageKey, collapsed ? "1" : "0");
      } catch (_error) {
        // Private browsing or a restricted storage policy should not block UI.
      }
    }
  };

  let initiallyCollapsed = false;
  try {
    initiallyCollapsed = window.localStorage.getItem(storageKey) === "1";
  } catch (_error) {
    initiallyCollapsed = false;
  }
  applyCollapsed(initiallyCollapsed, false);
  toggle.addEventListener("click", () => {
    applyCollapsed(toggle.getAttribute("aria-expanded") === "true");
  });
}

export function showToast(message) {
  dom.toast.textContent = message;
  dom.toast.classList.add("visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    dom.toast.classList.remove("visible");
  }, 3000);
}

export function setText(node, value) {
  const text = String(value ?? "");
  if (node.textContent !== text) node.textContent = text;
}
