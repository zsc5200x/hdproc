import { bindMyEventsEvents } from "./modules/my-events/bindings.js";
import { pollMyEventsEvents } from "./modules/my-events/page.js";
import { showPage } from "./modules/router.js";
import { loadSettingsPreferences } from "./modules/shared/preferences.js";
import { state } from "./modules/state.js";

bindMyEventsEvents();
loadSettingsPreferences();
if (location.hash) {
  // 设置页已移除，旧书签里的 #settings 直接清理掉。
  history.replaceState(null, "", `${location.pathname}${location.search}`);
}
showPage("my-events", false);

window.setInterval(() => {
  if (!document.hidden && state.currentPage === "my-events") {
    pollMyEventsEvents();
  }
}, 1000);
