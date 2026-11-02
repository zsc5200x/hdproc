import { fetchJson } from "../shared/http.js";

const library = {
  items: [],
  loaded: false,
  loading: false,
  query: "",
};

function byId(id) {
  return document.getElementById(id);
}

function cell(text) {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

function formatTime(value) {
  const text = String(value || "").trim();
  if (!text) return "—";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function countText(item) {
  const crown = Number(item?.crownEventCount || 0);
  const pinnacle = Number(item?.pinnacleEventCount || 0);
  return `${crown} / ${pinnacle}`;
}

function render() {
  const body = byId("myEventsLeagueBody");
  const empty = byId("myEventsLeagueEmpty");
  const count = byId("myEventsLeagueCount");
  if (!body) return;

  const query = library.query.trim().toLowerCase();
  const rows = library.items.filter((item) => {
    if (!query) return true;
    return [
      item.crownLeagueName,
      item.crownLeagueId,
      item.pinnacleLeagueName,
      item.pinnacleLeagueId,
    ].some((value) => String(value || "").toLowerCase().includes(query));
  });

  body.replaceChildren(...rows.map((item) => {
    const tr = document.createElement("tr");
    tr.append(
      cell(item.crownLeagueName || "—"),
      cell(String(item.crownLeagueId || "—")),
      cell(item.pinnacleLeagueName || "—"),
      cell(String(item.pinnacleLeagueId || "—")),
      cell(countText(item)),
      cell(formatTime(item.updatedAt)),
    );
    return tr;
  }));

  if (empty) empty.classList.toggle("hidden", rows.length > 0);
  if (count) {
    count.textContent = library.loading
      ? "读取中…"
      : `共 ${rows.length} 条${query ? `（全部 ${library.items.length} 条）` : ""}`;
  }
}

async function load(force = false) {
  if (library.loading) return;
  if (library.loaded && !force) {
    render();
    return;
  }
  library.loading = true;
  render();
  try {
    const { response, payload } = await fetchJson("/api/merge/league-dictionary", { cache: "no-store" });
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.error || `HTTP ${response.status}`);
    }
    library.items = Array.isArray(payload?.items) ? payload.items : [];
    library.loaded = true;
  } catch (error) {
    library.items = [];
    const count = byId("myEventsLeagueCount");
    if (count) count.textContent = `读取失败：${error?.message || "请求失败"}`;
  } finally {
    library.loading = false;
    render();
  }
}

function open() {
  const overlay = byId("myEventsLeagueLibrary");
  if (!overlay) return;
  overlay.classList.remove("hidden");
  document.body.classList.add("my-events-library-open");
  load();
  const search = byId("myEventsLeagueSearch");
  if (search) {
    search.value = library.query;
    window.setTimeout(() => search.focus(), 50);
  }
}

function close() {
  const overlay = byId("myEventsLeagueLibrary");
  if (!overlay) return;
  overlay.classList.add("hidden");
  document.body.classList.remove("my-events-library-open");
}

export function bindLeagueLibrary() {
  byId("myEventsLeagueLibraryBtn")?.addEventListener("click", open);
  byId("myEventsLeagueClose")?.addEventListener("click", close);
  byId("myEventsLeagueRefresh")?.addEventListener("click", () => load(true));
  byId("myEventsLeagueSearch")?.addEventListener("input", (event) => {
    library.query = String(event.target.value || "");
    render();
  });
  byId("myEventsLeagueLibrary")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !byId("myEventsLeagueLibrary")?.classList.contains("hidden")) {
      close();
    }
  });
}
