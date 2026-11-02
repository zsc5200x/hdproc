import {
  clearMyEventsBanner,
  loadMoreMyEvents,
  refreshMyEventsAll,
  refreshMyEventsPushes,
  setMyEventsView,
} from "./page.js";
import { state } from "../state.js";
import { bindLeagueLibrary } from "./league-library.js";

function setActiveTab(active) {
  document.querySelectorAll("[data-my-events-status]").forEach((button) => {
    const current = button === active;
    button.classList.toggle("active", current);
    button.setAttribute("aria-pressed", String(current));
  });
}

function markQuick(activeValue) {
  document.querySelectorAll("[data-my-events-range]").forEach((button) => {
    button.classList.toggle("active", button.dataset.myEventsRange === activeValue);
  });
}

function ymd(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function ymdInTz(offsetMinutes, date = new Date()) {
  const shifted = new Date(date.getTime() + Number(offsetMinutes) * 60000);
  return shifted.toISOString().slice(0, 10);
}

function addDaysYmd(text, days) {
  const date = new Date(`${text}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function applyRange(value) {
  const current = state.myEvents;
  const today = ymdInTz(current.tz);
  let from = "";
  let to = "";
  if (value === "today") {
    from = today;
    to = today;
  } else if (value === "24") {
    from = addDaysYmd(today, -1);
    to = today;
  } else if (value === "7" || value === "30") {
    from = addDaysYmd(today, -(Number(value) - 1));
    to = today;
  }
  current.from = from;
  current.to = to;
  current.hours = value === "24" ? 24 : 0;
  const fromInput = document.getElementById("myEventsFrom");
  const toInput = document.getElementById("myEventsTo");
  if (fromInput) fromInput.value = from;
  if (toInput) toInput.value = to;
  markQuick(value);
  clearMyEventsBanner();
  refreshMyEventsAll({ reset: true });
}

export function bindMyEventsEvents() {
  bindLeagueLibrary();
  const initialFrom = document.getElementById("myEventsFrom");
  const initialTo = document.getElementById("myEventsTo");
  if (initialFrom) initialFrom.value = state.myEvents.from;
  if (initialTo) initialTo.value = state.myEvents.to;
  const initialTz = document.getElementById("myEventsTz");
  if (initialTz) initialTz.value = String(state.myEvents.tz);
  markQuick("24");
  setActiveTab(document.querySelector('[data-my-events-status="ALL"]'));
  for (const item of document.querySelectorAll("[data-my-events-basis]")) {
    const active = item.dataset.myEventsBasis === state.myEvents.basis;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  }
  // 默认视角在 basis 初始化之后再应用；跟单/比赛视角会清掉 basis 的高亮。
  setMyEventsView(state.myEvents.view);

  document.querySelectorAll("[data-my-events-status]").forEach((button) => {
    button.addEventListener("click", () => {
      state.myEvents.status = button.dataset.myEventsStatus;
      state.myEvents.offset = 0;
      state.myEvents.fixtureOffset = 0;
      setActiveTab(button);
      clearMyEventsBanner();
      refreshMyEventsAll({ reset: true });
    });
  });

  const search = document.getElementById("myEventsSearch");
  let timer = null;
  search?.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      state.myEvents.search = search.value.trim();
      refreshMyEventsAll({ reset: true });
    }, 250);
  });

  const agent = document.getElementById("myEventsAgent");
  agent?.addEventListener("change", () => {
    state.myEvents.agent = agent.value;
    clearMyEventsBanner();
    refreshMyEventsAll({ reset: true });
  });

  document.getElementById("myEventsRefresh")?.addEventListener("click", () => {
    refreshMyEventsAll({ reset: true });
  });

  document.getElementById("myEventsMore")?.addEventListener("click", () => {
    loadMoreMyEvents();
  });

  document.getElementById("myEventsFixtureMore")?.addEventListener("click", () => {
    loadMoreMyEvents();
  });

  document.querySelector('[data-my-events-view="fixtures"]')?.addEventListener("click", () => {
    if (state.myEvents.view === "fixtures") return;
    setMyEventsView("fixtures");
    state.myEvents.fixtureOffset = 0;
    clearMyEventsBanner();
    refreshMyEventsAll({ reset: true });
  });

  document.querySelector('[data-my-events-view="pushes"]')?.addEventListener("click", () => {
    if (state.myEvents.view === "pushes") return;
    setMyEventsView("pushes");
    clearMyEventsBanner();
    refreshMyEventsPushes();
  });

  document.getElementById("myEventsFrom")?.addEventListener("change", (event) => {
    state.myEvents.from = event.target.value;
    state.myEvents.hours = 0;
    markQuick("");
    clearMyEventsBanner();
    refreshMyEventsAll({ reset: true });
  });

  document.getElementById("myEventsTo")?.addEventListener("change", (event) => {
    state.myEvents.to = event.target.value;
    state.myEvents.hours = 0;
    markQuick("");
    clearMyEventsBanner();
    refreshMyEventsAll({ reset: true });
  });

  document.querySelectorAll("[data-my-events-range]").forEach((button) => {
    button.addEventListener("click", () => applyRange(button.dataset.myEventsRange));
  });

  document.querySelectorAll("[data-my-events-basis]").forEach((button) => {
    button.addEventListener("click", () => {
      const basis = button.dataset.myEventsBasis;
      const backToOrders = state.myEvents.view !== "orders";
      setMyEventsView("orders");
      if (basis === state.myEvents.basis && !backToOrders) return;
      state.myEvents.basis = basis;
      for (const item of document.querySelectorAll("[data-my-events-basis]")) {
        const active = item.dataset.myEventsBasis === basis;
        item.classList.toggle("active", active);
        item.setAttribute("aria-pressed", String(active));
      }
      if (basis === "settled" && state.myEvents.status === "OPEN") {
        state.myEvents.status = "SETTLED";
        setActiveTab(document.querySelector('[data-my-events-status="SETTLED"]'));
      }
      state.myEvents.offset = 0;
      state.myEvents.fixtureOffset = 0;
      clearMyEventsBanner();
      refreshMyEventsAll({ reset: true });
    });
  });

  document.getElementById("myEventsTz")?.addEventListener("change", (event) => {
    state.myEvents.tz = Number(event.target.value) || 480;
    state.myEvents.offset = 0;
    state.myEvents.fixtureOffset = 0;
    clearMyEventsBanner();
    refreshMyEventsAll({ reset: true });
  });

  document.getElementById("myEventsMatchCompare")?.addEventListener("click", () => {
    if (state.myEvents.view === "fixtures") return;
    setMyEventsView("fixtures");
    state.myEvents.fixtureOffset = 0;
    clearMyEventsBanner();
    refreshMyEventsAll({ reset: true });
  });

  document.getElementById("myEventsAlignPartner")?.addEventListener("click", () => {
    const current = state.myEvents;
    const target = addDaysYmd(ymdInTz(-240), -1);
    setMyEventsView("orders");
    current.basis = "settled";
    current.tz = -240;
    current.from = target;
    current.to = target;
    current.status = "SETTLED";
    for (const item of document.querySelectorAll("[data-my-events-basis]")) {
      const active = item.dataset.myEventsBasis === "settled";
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    }
    const tzSelect = document.getElementById("myEventsTz");
    if (tzSelect) tzSelect.value = "-240";
    const fromInput = document.getElementById("myEventsFrom");
    const toInput = document.getElementById("myEventsTo");
    if (fromInput) fromInput.value = target;
    if (toInput) toInput.value = target;
    setActiveTab(document.querySelector('[data-my-events-status="SETTLED"]'));
    current.offset = 0;
    markQuick("");
    clearMyEventsBanner();
    refreshMyEventsAll({ reset: true });
  });
}
