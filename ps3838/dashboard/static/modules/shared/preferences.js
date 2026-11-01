import { dom } from "../dom.js";
import { state } from "../state.js";

export const SETTINGS_PREF_KEYS = {
  sort: "ps3838.dashboard.defaultSort",
  filter: "ps3838.dashboard.defaultFilter",
  history: "ps3838.dashboard.defaultHistory",
  changeLimit: "ps3838.dashboard.changeLimit",
};

export function readPreference(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch (_error) {
    return fallback;
  }
}

export function writePreference(key, value) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch (_error) {
    // A restricted storage policy should not prevent the dashboard from working.
  }
}

export function loadSettingsPreferences() {
  state.sort = ["stable", "kickoff", "name"].includes(
    readPreference(SETTINGS_PREF_KEYS.sort, "stable")
  ) ? readPreference(SETTINGS_PREF_KEYS.sort, "stable") : "stable";
  state.filter = ["all", "complete", "partial", "missing"].includes(
    readPreference(SETTINGS_PREF_KEYS.filter, "all")
  ) ? readPreference(SETTINGS_PREF_KEYS.filter, "all") : "all";
  state.historyHours = [1, 6, 24, 168, 720].includes(
    Number(readPreference(SETTINGS_PREF_KEYS.history, "24"))
  ) ? Number(readPreference(SETTINGS_PREF_KEYS.history, "24")) : 24;
  const requestedLimit = Number(readPreference(SETTINGS_PREF_KEYS.changeLimit, "20"));
  state.changeLimit = [8, 12, 20, 24].includes(requestedLimit) ? requestedLimit : 20;
}

export function syncPreferenceControls() {
  if (dom.sortSelect) dom.sortSelect.value = state.sort;
  if (dom.historyRangeSelect) dom.historyRangeSelect.value = String(state.historyHours);
}
