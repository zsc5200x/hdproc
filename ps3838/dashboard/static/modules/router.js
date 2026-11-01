import { dom } from "./dom.js";
import { refreshMyEventsAll } from "./my-events/page.js";
import { syncPreferenceControls } from "./shared/preferences.js";
import { resolvePage } from "./shared/routes.js";
import { state } from "./state.js";

const PAGE_TITLES = {
  "my-events": "我的赛事",
};

export function showPage(page, updateLocation = true) {
  const target = resolvePage(page);
  state.currentPage = target;

  for (const panel of dom.pagePanels) {
    panel.classList.toggle("hidden", panel.dataset.pagePanel !== target);
  }

  syncPreferenceControls();
  if (target === "my-events") refreshMyEventsAll({ reset: true });
  document.title = PAGE_TITLES[target] || "我的赛事";
  if (updateLocation) {
    history.replaceState(null, "", `${location.pathname}${location.search}#${target}`);
  }
  window.scrollTo(0, 0);
}
