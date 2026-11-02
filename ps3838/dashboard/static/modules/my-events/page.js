import { el } from "../shared/dom.js";
import { fetchJson } from "../shared/http.js";
import { state } from "../state.js";

const PAGE_SIZE = 50;
const FIXTURE_PAGE_SIZE = 50;
const BANNER_LIMIT = 5;
const BANNER_TTL_MS = 30000;

const $ = (id) => document.getElementById(id);

const RESULT_LABELS = {
  WIN: "胜",
  LOSE: "负",
  WIN_HALF: "赢半",
  LOSE_HALF: "输半",
  DRAW: "平",
  REFUNDED: "退还",
  OPEN: "未结算",
  "": "—",
};

function myEvents() {
  return state.myEvents;
}

function setStatus(text) {
  const node = $("myEventsStatus");
  if (node) node.textContent = text;
}

function formatNumber(value, digits = 0) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toLocaleString("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatMoney(value, _currency, digits = 0) {
  return formatNumber(value, digits);
}

function formatOdds(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "—";
}

function formatSigned(value, _currency) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const sign = number > 0 ? "+" : number < 0 ? "−" : "";
  return `${sign}${formatNumber(Math.abs(number), 2)}`;
}

function formatMember(row) {
  const code = row.member_user_code || "—";
  return row.member_name ? `${code} · ${row.member_name}` : code;
}

/** Render 0.25 / 0.75 as the Chinese quarter-line pair, e.g. 3.25 -> 3/3.5. */
function lineText(value) {
  const number = Math.abs(Number(value));
  if (!Number.isFinite(number)) return "";
  const whole = Math.floor(number);
  const fraction = Math.round((number - whole) * 100) / 100;
  if (fraction === 0) return String(whole);
  if (fraction === 0.5) return `${whole}.5`;
  if (fraction === 0.25) return `${whole}/${whole}.5`;
  if (fraction === 0.75) return `${whole}.5/${whole + 1}`;
  return String(number);
}

function signedLine(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  const text = lineText(number);
  if (number > 0) return `+${text}`;
  if (number < 0) return `-${text}`;
  return "0";
}

function splitTeams(value) {
  const text = String(value || "");
  const parts = text.split(/\s*-\s*vs\s*-\s*/i);
  if (parts.length === 2) return [parts[0].trim(), parts[1].trim()];
  return [text.trim(), ""];
}

/** Convert a raw market into the Chinese betting notation the user reads. */
function formatPick(row) {
  const market = String(row.market || "").trim();
  const lower = market.toLowerCase();
  const [home, away] = splitTeams(row.teams);

  if (lower.startsWith("ou") || market.startsWith("大小")) {
    const prefix = ["小", "UNDER"].includes(row.selection) ? "小" : "大";
    return `${prefix}${lineText(row.handicap)}`;
  }
  if (lower.startsWith("hdp") || market.startsWith("让球")) {
    const isHome = ["主", "HOME"].includes(row.selection);
    const team = isHome ? home : away;
    return `${team || (isHome ? "主队" : "客队")}${signedLine(row.handicap)}`;
  }
  if (lower.startsWith("1x2") || market.startsWith("独赢")) {
    return row.selection || "独赢";
  }
  if (lower.startsWith("or")) {
    return row.selection || market;
  }
  const label = market.replace(/\s*0(?:\.0+)?$/, "").trim();
  return [label, row.selection].filter(Boolean).join(" ") || "—";
}

function formatPeriod(row) {
  const rawType = row.period_type ?? row.periodType;
  const type = rawType === null || rawType === undefined || rawType === ""
    ? null
    : Number(rawType);
  if (type === 0) return "全场";
  if (type === 1) return "上半场";
  if (type === 2) return "下半场";
  const name = String(row.period_name ?? row.periodName ?? "").replace(/\s+/g, "").trim();
  return ["上半场", "下半场", "全场", "中场", "加时", "点球"].includes(name) ? name : "";
}

function formatPickWithPeriod(row) {
  const pick = formatPick(row);
  const period = formatPeriod(row);
  return period ? `${period} · ${pick}` : pick;
}

function formatTime(row, basis = "created", tzMinutes = -240) {
  const value = basis === "settled" ? row.settled_at : (row.created_ms ?? row.created_at);
  if (value == null || value === "") return "—";
  const date = new Date(typeof value === "number" ? value : Date.parse(value));
  if (Number.isNaN(date.getTime())) return "—";
  const timeZone = Number(tzMinutes) === -240 ? "Etc/GMT+4" : "Asia/Shanghai";
  return date.toLocaleString("zh-CN", { hour12: false, timeZone });
}

function resultClass(result) {
  const key = String(result || "open").toLowerCase().replace(/_/g, "-");
  return `result-badge result-${key}`;
}

function cell(text, className) {
  return el("td", className, text);
}

function resultCellFor(row) {
  const td = el("td", "result");
  td.append(el("span", resultClass(row.result), RESULT_LABELS[row.result] ?? "—"));
  if (row.inplay) td.append(el("span", "inplay", " · 滚球"));
  return td;
}

function renderRows() {
  const body = $("myEventsBody");
  if (!body) return;
  const rows = myEvents().rows;
  const basis = myEvents().basis || "created";
  const tzLabel = Number(myEvents().tz) === -240 ? "GMT-4" : "GMT+8";
  const timeHeader = $("myEventsTimeHeader");
  if (timeHeader) {
    timeHeader.textContent = `${basis === "settled" ? "结算时间" : "下单时间"} (${tzLabel})`;
  }
  body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.className = `my-events-row status-${String(row.status || "").toLowerCase()}`;

    const winLoss = row.win_loss == null ? null : Number(row.win_loss);
    const winLossCell = cell(
      row.status === "SETTLED" && winLoss != null ? formatSigned(winLoss) : "—",
      "winloss",
    );
    if (winLoss != null && winLoss > 0) winLossCell.classList.add("positive");
    else if (winLoss != null && winLoss < 0) winLossCell.classList.add("negative");

    tr.append(
      cell(row.league || "—", "league"),
      cell(row.teams || "—", "teams"),
      cell(formatPickWithPeriod(row), "market"),
      cell(formatOdds(row.odds), "odds"),
      cell(formatMoney(row.stake), "stake"),
      winLossCell,
      cell(row.order_id || "—", "order"),
      cell(row.agent_code || "—", "agent"),
      cell(formatMember(row), "member"),
      resultCellFor(row),
      cell(formatTime(row, basis, myEvents().tz), "time"),
    );
    body.append(tr);
  }
  const empty = $("myEventsEmpty");
  if (empty) empty.classList.toggle("hidden", rows.length > 0);
  const more = $("myEventsMore");
  if (more && myEvents().view === "orders") {
    more.classList.toggle("hidden", rows.length >= myEvents().total);
  }
}

function fixtureKey(row) {
  return `${row.teams}|${row.event_ms}`;
}

function formatKickoff(value, tzMinutes) {
  const date = new Date(Number(value));
  if (Number.isNaN(date.getTime())) return "—";
  const timeZone = Number(tzMinutes) === -240 ? "Etc/GMT+4" : "Asia/Shanghai";
  return date.toLocaleString("zh-CN", {
    hour12: false,
    timeZone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderFixtures() {
  const body = $("myEventsFixtureBody");
  if (!body) return;
  const current = myEvents();
  body.replaceChildren();
  for (const row of current.fixtures) {
    const key = fixtureKey(row);
    const tr = document.createElement("tr");
    tr.className = "my-events-fixture-row";
    if (key === current.fixtureOpenKey) tr.classList.add("open");

    const leagueCell = cell(row.league || "—", "league");
    leagueCell.append(el(
      "span",
      row.crown_league ? "crown-alias" : "crown-alias unmatched",
      row.crown_league ? `（${row.crown_league}）` : "（未匹配）",
    ));
    const teamsCell = cell(row.teams || "—", "teams");
    const missingLabel = row.crown_league ? "（皇冠暂无）" : "（未匹配）";
    const missingTitle = row.crown_league
      ? "皇冠当前赛事列表里没有这场，稍后会自动重试"
      : "没有找到对应的皇冠赛事";
    const teamsAlias = el(
      "span",
      row.crown_teams ? "crown-alias" : "crown-alias unmatched",
      row.crown_teams ? `（${row.crown_teams}）` : missingLabel,
    );
    if (!row.crown_teams) teamsAlias.title = missingTitle;
    teamsCell.append(teamsAlias);
    const lastMs = Number(row.last_ms) || 0;
    if (lastMs && Date.now() - lastMs <= 15000) {
      const fresh = el("span", "fixture-new", "新");
      fresh.title = "最近 15 秒有新单";
      teamsCell.append(fresh);
    }

    tr.append(
      leagueCell,
      teamsCell,
      cell(formatKickoff(row.event_ms, current.tz), "time"),
      cell(formatNumber(row.orders), "num"),
      cell(formatNumber(row.members), "num"),
      cell(formatNumber(row.lines), "num center"),
      cell(formatMoney(row.stake), "stake center"),
    );
    tr.addEventListener("click", () => toggleFixture(row));
    body.append(tr);
    if (key === current.fixtureOpenKey) body.append(renderFixtureDetail(row));
  }
  const empty = $("myEventsFixtureEmpty");
  if (empty) empty.classList.toggle("hidden", current.fixtures.length > 0);
  const emptyText = $("myEventsFixtureEmptyText");
  if (emptyText) emptyText.textContent = "当前范围内没有比赛";
  const more = $("myEventsFixtureMore");
  if (more) more.classList.toggle("hidden", current.fixtures.length >= current.fixtureTotal);
}

function renderFixtureDetail(row) {
  const current = myEvents();
  const key = fixtureKey(row);
  const orders = current.fixtureOrders.get(key);
  const crown = current.fixtureCrowns.get(key) || null;
  const tr = document.createElement("tr");
  tr.className = "my-events-fixture-detail";
  const td = document.createElement("td");
  td.colSpan = 7;

  const box = el("div", "fixture-detail");
  const head = el("div", "fixture-detail-head");
  const range = `${formatKickoff(row.first_ms, current.tz)} – ${formatKickoff(row.last_ms, current.tz)}`;
  if (orders === undefined) {
    head.textContent = "读取订单明细…";
  } else {
    const parts = [
      `${formatNumber(row.orders)} 单`,
      `${formatNumber(row.members)} 名会员`,
      `${formatNumber(row.lines)} 个盘口`,
      `金额 ${formatMoney(row.stake)}`,
    ];
    if (current.basis !== "settled") parts.push(`下单时间 ${range}`);
    if (crown && crown.snapshot_at) {
      const captured = new Date(crown.snapshot_at);
      if (!Number.isNaN(captured.getTime())) {
        parts.push(`皇冠快照 ${formatKickoff(captured.getTime(), current.tz)}`);
      }
    }
    head.textContent = parts.join(" · ");
  }
  box.append(head);

  if (orders && orders.length) {
    const groups = groupOrdersByPick(orders, current.basis);
    const grouped = document.createElement("table");
    grouped.className = "fixture-order-table fixture-line-table";
    const groupedHead = document.createElement("thead");
    const groupedHeadRow = document.createElement("tr");
    for (const label of ["盘口", "数量", "水位", "皇冠对手", "合水", "比分", "金额", "会员", "时间"]) {
      const th = el("th", "", label);
      if (label === "水位") th.title = "该盘口组的水位，多个不同水位显示为区间";
      if (label === "比分") th.title = "下注时的比分（滚球）";
      if (label === "皇冠对手") th.title = "皇冠同场同线的对手盘水位（例如平博小2.5 → 皇冠大2.5）";
      if (label === "合水") th.title = "平博水位 + 皇冠对手水位";
      groupedHeadRow.append(th);
    }
    groupedHead.append(groupedHeadRow);
    const groupedBody = document.createElement("tbody");
    for (const group of groups) {
      const groupRow = document.createElement("tr");
      const timeText = group.first == null
        ? "—"
        : `${formatKickoff(group.first, current.tz)} – ${formatKickoff(group.last, current.tz)}`;
      const opposite = crownOppositeWater({ orders: [group.sample] }, crown);
      const oppositeCell = cell(
        opposite ? `${opposite.label} ${formatOdds(opposite.water)}` : "—",
        "crown-opposite",
      );
      if (!opposite) {
        oppositeCell.title = "皇冠当前没有挂同一条线，或该场未实时匹配到皇冠";
      }
      const combined = opposite
        ? combinedWater(group.oddsMin, group.oddsMax, opposite.water)
        : "—";
      groupRow.append(
        cell(group.pick, "market"),
        cell(formatNumber(group.count), "num"),
        cell(formatOddsRange(group), "odds"),
        oppositeCell,
        cell(combined, "combined-water"),
        cell(formatScoreRange(group), "score"),
        cell(formatMoney(group.stake), "stake"),
        cell(formatNumber(group.members.size), "num"),
        cell(timeText, "time"),
      );
      groupedBody.append(groupRow);
    }
    grouped.append(groupedHead, groupedBody);
    box.append(grouped);

    const details = document.createElement("details");
    details.className = "fixture-orders";
    const clusters = clusterOrdersByBurst(orders);
    const summary = document.createElement("summary");
    summary.textContent = `逐单明细（${formatNumber(orders.length)} 单 · 合并 ${formatNumber(clusters.length)} 组）`;
    details.append(summary);
    details.append(renderClusterTable(clusters, current, crown));
    box.append(details);

    if (Number(row.orders) > orders.length) {
      box.append(el("div", "fixture-detail-note", `仅显示前 ${orders.length} 单`));
    }
  } else if (orders) {
    box.append(el("div", "fixture-detail-note", "该筛选条件下没有订单"));
  }

  td.append(box);
  tr.append(td);
  tr.addEventListener("click", (event) => event.stopPropagation());
  return tr;
}

function groupOrdersByPick(orders, basis = "created") {
  const map = new Map();
  for (const order of orders) {
    const pick = formatPickWithPeriod(order);
    let group = map.get(pick);
    if (!group) {
      group = {
        pick, count: 0, stake: 0, members: new Set(), first: null, last: null,
        oddsMin: null, oddsMax: null, scoreFirst: "", scoreLast: "", sample: order,
      };
      map.set(pick, group);
    }
    group.count += 1;
    group.stake += Number(order.stake) || 0;
    group.members.add(order.member_user_code || order.member_name || order.order_id || "");
    const odds = Number(order.odds);
    if (Number.isFinite(odds)) {
      if (group.oddsMin == null || odds < group.oddsMin) group.oddsMin = odds;
      if (group.oddsMax == null || odds > group.oddsMax) group.oddsMax = odds;
    }
    const score = String(order.score || "").trim();
    if (score) {
      if (!group.scoreFirst) group.scoreFirst = score;
      group.scoreLast = score;
    }
    const value = basis === "settled" ? Date.parse(order.settled_at) : Number(order.created_ms);
    if (Number.isFinite(value)) {
      if (group.first == null || value < group.first) group.first = value;
      if (group.last == null || value > group.last) group.last = value;
    }
  }
  return [...map.values()].sort((a, b) => b.count - a.count || a.pick.localeCompare(b.pick));
}

function formatOddsRange(group) {
  if (group.oddsMin == null) return "—";
  const min = formatOdds(group.oddsMin);
  const max = formatOdds(group.oddsMax);
  return min === max ? min : `${min}–${max}`;
}

function formatScoreRange(group) {
  if (!group.scoreFirst) return "—";
  return group.scoreFirst === group.scoreLast
    ? group.scoreFirst
    : `${group.scoreFirst} → ${group.scoreLast}`;
}

/** Merge orders on the same pick + same score that arrive within 20 seconds. */
function clusterOrdersByBurst(orders) {
  const sorted = [...orders].sort(
    (a, b) => (Number(a.created_ms) || 0) - (Number(b.created_ms) || 0),
  );
  const clusters = [];
  for (const order of sorted) {
    const pick = formatPickWithPeriod(order);
    const score = String(order.score || "").trim();
    const ms = Number(order.created_ms) || 0;
    const last = clusters[clusters.length - 1];
    if (last && last.pick === pick && last.score === score && ms - last.startMs <= 20000) {
      last.orders.push(order);
      last.endMs = ms;
    } else {
      clusters.push({ pick, score, startMs: ms, endMs: ms, orders: [order] });
    }
  }
  return clusters;
}

function formatClock(ms, tzMinutes) {
  const date = new Date(Number(ms));
  if (Number.isNaN(date.getTime())) return "—";
  const timeZone = Number(tzMinutes) === -240 ? "Etc/GMT+4" : "Asia/Shanghai";
  return date.toLocaleTimeString("zh-CN", { hour12: false, timeZone });
}

function oddsBounds(orders) {
  let min = null;
  let max = null;
  for (const order of orders) {
    const value = Number(order.odds);
    if (!Number.isFinite(value)) continue;
    if (min == null || value < min) min = value;
    if (max == null || value > max) max = value;
  }
  return { min, max };
}

function oddsRangeForOrders(orders) {
  const { min, max } = oddsBounds(orders);
  if (min == null) return "—";
  const low = formatOdds(min);
  const high = formatOdds(max);
  return low === high ? low : `${low}–${high}`;
}

/** Pinnacle water + Crown opposite water (合水). */
function combinedWater(minOdds, maxOdds, crownWater) {
  if (minOdds == null || crownWater == null || !Number.isFinite(Number(crownWater))) {
    return "—";
  }
  const low = Number(minOdds) + Number(crownWater);
  const high = Number(maxOdds == null ? minOdds : maxOdds) + Number(crownWater);
  const lowText = formatNumber(low, 2);
  const highText = formatNumber(high, 2);
  return lowText === highText ? lowText : `${lowText}–${highText}`;
}

function renderClusterTable(clusters, current, crown) {
  const table = document.createElement("table");
  table.className = "fixture-order-table fixture-cluster-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["下单时间", "盘口", "比分", "水位", "皇冠对手", "合水", "金额", "单数", "会员"]) {
    const th = el("th", "", label);
    if (label === "比分") th.title = "下注时的比分（滚球）";
    if (label === "金额") th.title = "20 秒内同盘口、同比分订单的金额合计";
    if (label === "皇冠对手") th.title = "皇冠同场同线的对手盘水位（例如平博小2.5 → 皇冠大2.5）";
    if (label === "合水") th.title = "平博水位 + 皇冠对手水位";
    if (label === "皇冠对手") th.title = "皇冠同场同线的对手盘水位（例如平博小2.5 → 皇冠大2.5）";
    headRow.append(th);
  }
  thead.append(headRow);

  const tbody = document.createElement("tbody");
  for (const cluster of clusters) {
    const row = document.createElement("tr");
    row.className = "cluster-row";
    const first = cluster.orders[0];
    const startText = formatTime(first, "created", current.tz);
    const timeText = cluster.endMs > cluster.startMs
      ? `${startText}–${formatClock(cluster.endMs, current.tz)}`
      : startText;
    const stake = cluster.orders.reduce((sum, order) => sum + (Number(order.stake) || 0), 0);
    const members = new Set(
      cluster.orders.map((order) => order.member_user_code || order.member_name || order.order_id || ""),
    );
    const opposite = crownOppositeWater(cluster, crown);
    const oppositeCell = cell(
      opposite ? `${opposite.label} ${formatOdds(opposite.water)}` : "—",
      "crown-opposite",
    );
    if (!opposite) {
      oppositeCell.title = "皇冠当前没有挂同一条线，或该场未实时匹配到皇冠";
    }
    const bounds = oddsBounds(cluster.orders);
    const combined = opposite
      ? combinedWater(bounds.min, bounds.max, opposite.water)
      : "—";
    row.append(
      cell(timeText, "time"),
      cell(cluster.pick, "market"),
      cell(cluster.score || "—", "score"),
      cell(oddsRangeForOrders(cluster.orders), "odds"),
      oppositeCell,
      cell(combined, "combined-water"),
      cell(formatMoney(stake), "stake"),
      cell(formatNumber(cluster.orders.length), "num"),
      cell(formatNumber(members.size), "num"),
    );

    const detailRow = document.createElement("tr");
    detailRow.className = "cluster-orders hidden";
    const detailCell = document.createElement("td");
    detailCell.colSpan = 9;
    detailCell.append(renderClusterOrders(cluster.orders, current));
    detailRow.append(detailCell);

    row.addEventListener("click", () => {
      const open = detailRow.classList.toggle("hidden");
      row.classList.toggle("open", !open);
    });
    tbody.append(row, detailRow);
  }
  table.append(thead, tbody);
  return table;
}

/** Parse Crown line text such as "2.5", "3", "+0 / 0.5", "-1". */
function parseCrownLine(value) {
  const text = String(value == null ? "" : value).trim()
    .replace(/[\u2212\u2013\u2014]/g, "-")
    .replace(/\s+/g, "");
  if (!text) return null;
  const parts = text.split("/");
  if (parts.length > 2 || parts.some((part) => part === "")) return null;
  const numbers = parts.map((part) => Number(part));
  if (numbers.some((number) => !Number.isFinite(number))) return null;
  if (numbers.length === 2 && parts[0].startsWith("-") && !parts[1].startsWith("+") && !parts[1].startsWith("-")) {
    numbers[1] = -Math.abs(numbers[1]);
  }
  if (numbers.length === 2 && parts[0].startsWith("+") && !parts[1].startsWith("+") && !parts[1].startsWith("-")) {
    numbers[1] = Math.abs(numbers[1]);
  }
  return numbers.reduce((sum, number) => sum + number, 0) / numbers.length;
}

/** Crown water for the opposite selection on the same line. */
function crownOppositeWater(cluster, crown) {
  if (!crown || !crown.markets) return null;
  const order = cluster.orders[0];
  const market = String(order.market || "");
  const selection = String(order.selection || "").toUpperCase();
  const line = Number(order.handicap);
  if (!Number.isFinite(line)) return null;

  const period = Number(order.period_type);
  if (period !== 0 && period !== 1) return null;

  if (market.startsWith("大小") || market.startsWith("OU")) {
    const items = period === 1 ? (crown.markets.half_total || []) : (crown.markets.total || []);
    const item = items.find(
      (entry) => Math.abs(parseCrownLine(entry.line) - line) < 0.01,
    );
    if (!item) return null;
    const isUnder = selection === "小" || selection === "UNDER";
    const water = Number(isUnder ? item.a : item.b);
    if (!Number.isFinite(water)) return null;
    const label = `${isUnder ? "大" : "小"}${lineText(line)}`;
    return { label, water };
  }

  if (market.startsWith("让球") || market.startsWith("HDP")) {
    const isHome = selection === "主" || selection === "HOME";
    const homeLine = isHome ? line : -line;
    const items = period === 1 ? (crown.markets.half_handicap || []) : (crown.markets.handicap || []);
    const item = items.find(
      (entry) => Math.abs(parseCrownLine(entry.line) - homeLine) < 0.01,
    );
    if (!item) return null;
    const water = Number(isHome ? item.b : item.a);
    if (!Number.isFinite(water)) return null;
    const team = isHome ? crown.away : crown.home;
    const signed = -line;
    return { label: `${team || (isHome ? "客队" : "主队")}${signedLine(signed)}`, water };
  }
  return null;
}

function renderClusterOrders(orders, current) {
  const table = document.createElement("table");
  table.className = "fixture-order-table cluster-orders-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["下单时间", "水位", "金额", "会员", "结果", "订单号"]) {
    headRow.append(el("th", "", label));
  }
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  for (const order of orders) {
    const row = document.createElement("tr");
    row.append(
      cell(formatTime(order, current.basis, current.tz), "time"),
      cell(formatOdds(order.odds), "odds"),
      cell(formatMoney(order.stake), "stake"),
      cell(formatMember(order), "member"),
      resultCellFor(order),
      cell(order.order_id || "—", "order"),
    );
    tbody.append(row);
  }
  table.append(thead, tbody);
  return table;
}

function toggleFixture(row) {
  const current = myEvents();
  const key = fixtureKey(row);
  if (current.fixtureOpenKey === key) {
    current.fixtureOpenKey = "";
    renderFixtures();
    return;
  }
  current.fixtureOpenKey = key;
  renderFixtures();
  if (!current.fixtureOrders.has(key)) loadFixtureOrders(row);
}

async function loadFixtureOrders(row) {
  const current = myEvents();
  const key = fixtureKey(row);
  try {
    const { response, payload } = await fetchJson(
      `/api/my-events/fixture?${fixtureOrderQuery(row)}`,
      { cache: "no-store" },
      { timeoutMs: 10000, allowInvalidJson: true },
    );
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    current.fixtureOrders.set(key, Array.isArray(payload.rows) ? payload.rows : []);
    current.fixtureCrowns.set(key, payload.crown || null);
  } catch (_error) {
    current.fixtureOrders.set(key, []);
    current.fixtureCrowns.set(key, null);
  }
  if (current.fixtureOpenKey === key) renderFixtures();
}

function renderAgentOptions() {
  const select = $("myEventsAgent");
  if (!select) return;
  const agents = myEvents().agents || [];
  const signature = agents.map((agent) => agent.code).join(",");
  if (select.dataset.signature === signature) return;
  select.dataset.signature = signature;
  const current = select.value;
  select.replaceChildren(new Option("全部代理", ""));
  for (const agent of agents) {
    select.append(new Option(agent.code, agent.code));
  }
  select.value = current;
}

function setStat(id, text, tone = "") {
  const node = $(id);
  if (!node) return;
  node.textContent = text;
  node.classList.remove("positive", "negative", "neutral");
  if (tone) node.classList.add(tone);
}

function renderAgents(agents) {
  const box = $("myEventsAgents");
  if (!box) return;
  box.replaceChildren();
  for (const agent of agents) {
    const card = el("article", "my-events-agent");
    card.append(el("span", "my-events-agent-code", agent.agent_code || "—"));
    card.append(el("strong", "", formatMoney(agent.stake)));
    const net = Number(agent.net);
    const foot = el(
      "span",
      "my-events-agent-foot",
      `${formatNumber(agent.orders)} 单 · 输赢 ${formatSigned(net)}`,
    );
    if (net > 0) foot.classList.add("positive");
    else if (net < 0) foot.classList.add("negative");
    card.append(foot);
    box.append(card);
  }
}

function renderSummary(payload) {
  setStat("myEventsTotalStake", formatMoney(payload.total_stake));
  setStat(
    "myEventsTotalOrders",
    `${formatNumber(payload.orders)} 单 · 已结算 ${formatNumber(payload.settled)} · 未结算 ${formatNumber(payload.open)}`,
  );
  const net = Number(payload.net);
  setStat(
    "myEventsNet",
    formatSigned(net),
    net > 0 ? "positive" : net < 0 ? "negative" : "neutral",
  );
  setStat(
    "myEventsWinLose",
    `赢 ${formatNumber(payload.win_count)} · 输 ${formatNumber(payload.lose_count)} · 赢半 ${formatNumber(payload.win_half_count)} · 输半 ${formatNumber(payload.lose_half_count)}`,
  );
  setStat("myEventsOpenCount", formatNumber(payload.open));
  setStat("myEventsOpenStake", formatMoney(payload.open_stake));
  setStat("myEventsSettledCount", formatNumber(payload.settled));
  setStat("myEventsSettledStake", formatMoney(payload.settled_stake));
  renderAgents(payload.agents || []);
}

function summaryQuery() {
  const current = myEvents();
  const params = new URLSearchParams();
  if (current.agent) params.set("agent", current.agent);
  if (current.from) params.set("from", current.from);
  if (current.to) params.set("to", current.to);
  if (current.hours) params.set("hours", String(current.hours));
  params.set("basis", current.basis || "created");
  params.set("tz", String(current.tz ?? 480));
  return params.toString();
}

export function clearMyEventsBanner() {
  const banner = $("myEventsBanner");
  myEvents().banner = [];
  window.clearTimeout(myEvents().bannerTimer);
  if (banner) {
    banner.replaceChildren();
    banner.classList.add("hidden");
  }
}

export function showMyEventsBanner(events) {
  const banner = $("myEventsBanner");
  if (!banner || !Array.isArray(events) || !events.length) return;
  const current = myEvents();
  current.banner = [...events, ...current.banner].slice(0, BANNER_LIMIT);
  banner.replaceChildren();
  for (const event of current.banner) {
    const chip = el("span", "my-events-banner-chip");
    chip.append(el("b", "", "新单 "));
    chip.append(document.createTextNode(
      `${event.member_user_code || "会员"} · ${event.agent_code || "代理"} · `
      + `${event.league || "联赛"} · ${event.teams || "比赛"} · `
      + `${[event.market, event.selection].filter(Boolean).join(" ") || "盘口"} · `
      + `水位 ${formatOdds(event.odds)} · 金额 ${formatMoney(event.stake)} · `
      + `#${event.order_id || "—"}`
    ));
    banner.append(chip);
  }
  const close = el("button", "my-events-banner-close", "关闭");
  close.type = "button";
  close.addEventListener("click", clearMyEventsBanner);
  banner.append(close);
  banner.classList.remove("hidden");
  window.clearTimeout(current.bannerTimer);
  current.bannerTimer = window.setTimeout(clearMyEventsBanner, BANNER_TTL_MS);
}

function scopeParams() {
  const current = myEvents();
  const params = new URLSearchParams();
  params.set("status", current.status);
  if (current.agent) params.set("agent", current.agent);
  if (current.search) params.set("search", current.search);
  if (current.from) params.set("from", current.from);
  if (current.to) params.set("to", current.to);
  if (current.hours) params.set("hours", String(current.hours));
  params.set("basis", current.basis || "created");
  params.set("tz", String(current.tz ?? 480));
  return params;
}

export function myEventsQuery() {
  const params = scopeParams();
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(myEvents().offset));
  return params.toString();
}

function fixturesQuery() {
  const current = myEvents();
  const params = scopeParams();
  params.set("limit", String(FIXTURE_PAGE_SIZE));
  params.set("offset", String(current.fixtureOffset));
  return params.toString();
}

function fixtureOrderQuery(row) {
  const params = scopeParams();
  params.set("teams", row.teams);
  params.set("event", String(row.event_ms));
  return params.toString();
}

let listRequestId = 0;

export async function refreshMyEvents({ reset = false, silent = false } = {}) {
  const current = myEvents();
  if (current.loading && !reset) return;
  const requestId = ++listRequestId;
  if (reset) current.offset = 0;
  current.loading = true;
  if (!silent) setStatus("读取中…");
  try {
    const { response, payload } = await fetchJson(
      `/api/my-events?${myEventsQuery()}`,
      { cache: "no-store" },
      { timeoutMs: 10000, allowInvalidJson: true },
    );
    if (requestId !== listRequestId) return;
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    current.rows = reset ? rows : current.rows.concat(rows);
    current.total = Number(payload.total || 0);
    current.ready = Boolean(payload.ready);
    if (Array.isArray(payload.agents)) current.agents = payload.agents;
    renderAgentOptions();
    renderRows();
    const count = $("myEventsCount");
    if (count) count.textContent = `${current.total} 单`;
    setStatus(current.ready ? `共 ${current.total} 单` : "采集器未启动");
  } catch (error) {
    if (!silent) setStatus(`读取失败：${error.message || "请求失败"}`);
  } finally {
    if (requestId === listRequestId) current.loading = false;
  }
}

let fixtureRequestId = 0;

export async function refreshFixtures({ reset = false, silent = false, keepOpen = false } = {}) {
  const current = myEvents();
  if (current.fixtureLoading && !reset) return;
  const requestId = ++fixtureRequestId;
  if (reset) {
    current.fixtureOffset = 0;
    current.fixtureLastRefreshAt = Date.now();
    if (!keepOpen) {
      current.fixtureOrders = new Map();
      current.fixtureCrowns = new Map();
      current.fixtureOpenKey = "";
    }
  }
  current.fixtureLoading = true;
  if (!silent) setStatus("读取中…");
  const scrollY = keepOpen ? window.scrollY : 0;
  const wrap = keepOpen ? $("myEventsFixtureWrap") : null;
  const wrapTop = wrap ? wrap.scrollTop : 0;
  try {
    const { response, payload } = await fetchJson(
      `/api/my-events/fixtures?${fixturesQuery()}`,
      { cache: "no-store" },
      { timeoutMs: 20000, allowInvalidJson: true },
    );
    if (requestId !== fixtureRequestId) return;
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    current.fixtures = reset ? rows : current.fixtures.concat(rows);
    current.fixtureTotal = Number(payload.total || 0);
    current.ready = Boolean(payload.ready);
    if (Array.isArray(payload.agents)) current.agents = payload.agents;
    renderAgentOptions();
    renderFixtures();
    if (keepOpen && current.fixtureOpenKey) {
      const openRow = current.fixtures.find((item) => fixtureKey(item) === current.fixtureOpenKey);
      if (openRow) {
        loadFixtureOrders(openRow);
      } else {
        current.fixtureOpenKey = "";
        renderFixtures();
      }
    }
    if (keepOpen) {
      if (wrap) wrap.scrollTop = wrapTop;
      window.scrollTo(0, scrollY);
    }
    const count = $("myEventsCount");
    if (count) count.textContent = `${formatNumber(current.fixtureTotal)} 场`;
    setStatus(current.ready ? `共 ${current.fixtureTotal} 场` : "采集器未启动");
  } catch (error) {
    if (!silent) setStatus(`读取失败：${error.message || "请求失败"}`);
  } finally {
    if (requestId === fixtureRequestId) current.fixtureLoading = false;
  }
}

export function setMyEventsView(view) {
  const current = myEvents();
  const next = view === "fixtures" ? "fixtures" : view === "pushes" ? "pushes" : view === "compare" ? "compare" : "orders";
  current.view = next;

  for (const item of document.querySelectorAll("[data-my-events-view]")) {
    const active = item.dataset.myEventsView === next;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  }
  if (next !== "orders") {
    for (const item of document.querySelectorAll("[data-my-events-basis]")) {
      item.classList.remove("active");
      item.setAttribute("aria-pressed", "false");
    }
  }

  const fixtures = next === "fixtures";
  const pushes = next === "pushes";
  const compare = next === "compare";
  $("myEventsOrdersWrap")?.classList.toggle("hidden", fixtures || pushes || compare);
  $("myEventsFixtureWrap")?.classList.toggle("hidden", !fixtures);
  $("myEventsPushWrap")?.classList.toggle("hidden", !pushes);
  $("myEventsCompareWrap")?.classList.toggle("hidden", !compare);
  const ordersMore = $("myEventsMore");
  if (ordersMore) {
    ordersMore.classList.toggle("hidden", fixtures || pushes || compare || current.rows.length >= current.total);
  }
  const fixtureMore = $("myEventsFixtureMore");
  if (fixtureMore) {
    fixtureMore.classList.toggle("hidden", !fixtures || current.fixtures.length >= current.fixtureTotal);
  }
}

let summaryRequestId = 0;

export async function refreshMyEventsSummary() {
  const requestId = ++summaryRequestId;
  try {
    const { response, payload } = await fetchJson(
      `/api/my-events/summary?${summaryQuery()}`,
      { cache: "no-store" },
      { timeoutMs: 10000, allowInvalidJson: true },
    );
    if (requestId !== summaryRequestId) return;
    if (!response.ok || payload.ok === false) return;
    if (payload.ready === false) return;
    renderSummary(payload);
  } catch (_error) {
    // Keep the previous cards when the local summary query is unavailable.
  }
}

function formatPushTime(value, tzMinutes) {
  const date = typeof value === "number" ? new Date(value) : new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) return "—";
  const timeZone = Number(tzMinutes) === -240 ? "Etc/GMT+4" : "Asia/Shanghai";
  return date.toLocaleString("zh-CN", { hour12: false, timeZone });
}

function plainText(value) {
  const holder = document.createElement("div");
  holder.innerHTML = String(value || "");
  return (holder.textContent || "").replace(/\s+/g, " ").trim();
}

function pushQuery() {
  const current = myEvents();
  const params = new URLSearchParams();
  if (current.agent) params.set("agent", current.agent);
  if (current.from) params.set("from", current.from);
  if (current.to) params.set("to", current.to);
  if (current.search) params.set("search", current.search);
  params.set("basis", current.basis || "created");
  params.set("tz", String(current.tz ?? -240));
  params.set("limit", "200");
  return params.toString();
}

function pushGroupKey(row) {
  // 同一条合并推送的每条订单共享同一段推送文本；没有文本的排队/失败单各自成行。
  const text = String(row.pushText || "").trim();
  return text ? `t:${text}` : `e:${row.eventId}`;
}

function pushStatusMeta(rows) {
  const statuses = rows.map((row) => String(row.pushStatus || "").toLowerCase());
  if (statuses.includes("failed")) return { status: "failed", label: "失败" };
  if (statuses.includes("queued")) return { status: "queued", label: "排队中" };
  if (statuses.includes("sent")) return { status: "sent", label: "已推送" };
  const status = statuses[0] || "";
  return { status, label: status || "—" };
}

// ── 跟单上下文：从 HD（同源 9999）读取合并单与跟单结果 ──────────────
let hdFollowOrders = [];
let hdFollowResults = [];

async function readRootJson(url) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) return null;
    return await response.json();
  } catch (_error) {
    return null;
  }
}

async function loadFollowContext() {
  const [ordersPayload, resultsPayload] = await Promise.all([
    readRootJson("/api/orders?since=0&limit=500&target=ps3838"),
    readRootJson("/api/follow-results?limit=200"),
  ]);
  hdFollowOrders = Array.isArray(ordersPayload?.orders) ? ordersPayload.orders : [];
  hdFollowResults = Array.isArray(resultsPayload?.results) ? resultsPayload.results : [];
}

function marketKindOfRow(row) {
  const market = String(row?.market || "").toLowerCase();
  if (market.startsWith("hdp") || market.includes("让球")) return "hdp";
  if (market.startsWith("ou") || market.includes("大小")) return "ou";
  return "";
}

function marketKindOfOrder(order) {
  const text = `${order?.betType || ""} ${order?.raw?.WAGERSTYPE || ""}`;
  if (text.includes("让球")) return "hdp";
  if (text.includes("大小")) return "ou";
  return "";
}

function matchFollowOrders(group) {
  // 分单的 stake 来自这一组的合计；一组可能被拆成多个分单，按时间窗口求和对上。
  const kind = marketKindOfRow(group.first);
  const groupTime = Date.parse(group.pushedAt || "");
  if (!Number.isFinite(groupTime)) return [];
  const candidates = hdFollowOrders
    .filter((order) => {
      const ts = Number(order?.ts) * 1000;
      if (!Number.isFinite(ts)) return false;
      if (Math.abs(ts - groupTime) > 10 * 60 * 1000) return false;
      if (kind && marketKindOfOrder(order) !== kind) return false;
      return true;
    })
    .sort((a, b) => Number(a.ts) - Number(b.ts));
  const exact = candidates.filter((order) => Math.abs(Number(order.stake) - group.stake) <= 0.5);
  if (exact.length) {
    exact.sort(
      (a, b) =>
        Math.abs(Number(a.ts) * 1000 - groupTime) - Math.abs(Number(b.ts) * 1000 - groupTime),
    );
    return [exact[0]];
  }
  for (const base of candidates) {
    const baseTs = Number(base.ts);
    const cluster = candidates.filter(
      (order) => Number(order.ts) >= baseTs && Number(order.ts) <= baseTs + 10,
    );
    const total = cluster.reduce((sum, order) => sum + (Number(order.stake) || 0), 0);
    if (Math.abs(total - group.stake) <= 0.5) return cluster;
  }
  // 兜底：桥接可能把相邻的几个推送组合并成一个更大的分单，取时间最近且金额覆盖该组的单。
  const near = candidates
    .filter((order) => Number(order.stake) + 0.5 >= group.stake)
    .sort(
      (a, b) =>
        Math.abs(Number(a.ts) * 1000 - groupTime) - Math.abs(Number(b.ts) * 1000 - groupTime),
    );
  if (near.length && Math.abs(Number(near[0].ts) * 1000 - groupTime) <= 2 * 60 * 1000) {
    return [near[0]];
  }
  return [];
}

function opponentText(orders) {
  const labels = [];
  for (const order of orders) {
    const line = String(order.display_line || order.line || "").trim();
    if (!line) continue;
    const key = line.replace(/\s+/g, "");
    if (labels.some((item) => item.key === key)) continue;
    labels.push({ key, text: `${line}${order.odds ? ` @${order.odds}` : ""}` });
  }
  return labels.map((item) => item.text).join(" / ") || "—";
}

function followSummary(orders) {
  if (!orders.length) {
    return { label: "未匹配", tone: "none", detail: "没有找到对应的跟单订单" };
  }
  const latest = new Map();
  const stampOf = (item) => Number(item?.ts_ms ?? (Number(item?.ts) || 0) * 1000) || 0;
  for (const order of orders) {
    for (const item of hdFollowResults) {
      if (String(item?.orderId) !== String(order.orderId)) continue;
      const key = `${order.orderId}:${item.member || item.member_id || "?"}`;
      const previous = latest.get(key);
      // 接口按时间倒序返回，这里显式取每个账号最新的一次结果。
      if (!previous || stampOf(item) >= stampOf(previous)) latest.set(key, item);
    }
  }
  const values = [...latest.values()];
  if (!values.length) {
    return { label: "待跟单", tone: "queued", detail: "订单已生成，暂无跟单结果", successStake: 0, totalStake: 0 };
  }
  const stakeOf = (item) => {
    const amount = Number(item?.stake);
    return Number.isFinite(amount) ? amount : 0;
  };
  const okValues = values.filter((item) => item.ok);
  const ok = okValues.length;
  const total = values.length;
  const totalStake = values.reduce((sum, item) => sum + stakeOf(item), 0);
  const successStake = okValues.reduce((sum, item) => sum + stakeOf(item), 0);
  const detail = values
    .map((item) => `${item.member || "?"}：${item.ok ? "成功" : "失败"}${stakeOf(item) ? ` ${formatMoney(stakeOf(item))}` : ""}`)
    .join("\n");
  const stakeText = successStake === totalStake
    ? formatMoney(totalStake)
    : `${formatMoney(successStake)} / ${formatMoney(totalStake)}`;
  // 跟单程序只要报成功就显示成功，不再区分“部分成功”。
  if (ok > 0) {
    return {
      label: "成功",
      tone: "ok",
      detail: `${detail}\n成功 ${ok}/${total} 个跟单账号 · 跟单金额 ${stakeText}`,
      successStake,
      totalStake,
    };
  }
  return {
    label: "失败",
    tone: "fail",
    detail: `${detail}\n跟单金额 ${stakeText}`,
    successStake,
    totalStake,
  };
}

function followOrderRows() {
  const current = myEvents();
  const from = String(current.from || "");
  const to = String(current.to || "");
  const tz = Number(current.tz ?? -240);
  const search = String(current.search || "").toLowerCase();
  const rows = [];
  for (const order of hdFollowOrders) {
    const ts = Number(order?.ts);
    if (!Number.isFinite(ts)) continue;
    const date = new Date(ts * 1000 + tz * 60_000).toISOString().slice(0, 10);
    if (from && date < from) continue;
    if (to && date > to) continue;
    if (search) {
      const text = [order.league, order.sides, order.orderId, order.line, order.display_line, order.betType]
        .map((value) => String(value || ""))
        .join(" ")
        .toLowerCase();
      if (!text.includes(search)) continue;
    }
    rows.push(order);
  }
  rows.sort((a, b) => Number(b.ts) - Number(a.ts));
  return rows;
}

function formatFollowBetType(order) {
  const text = String(order?.betType || order?.raw?.WAGERSTYPE || "").trim();
  if (!text) return "—";
  // 有 "(滚球)" 前缀的是滚球；没有前缀的是赛前（源单 inplay=0）。
  const live = /滚球/.test(text);
  const body = text
    .replace(/[（(]滚球[)）]\s*/g, "")
    .replace(/^滚球\s*[·.]?\s*/, "")
    .trim();
  return `${live ? "滚球" : "赛前"} · ${body || "—"}`;
}

function subsetForAmount(rows, target) {
  const list = rows.slice(0, 12);
  const n = list.length;
  for (let mask = 1; mask < 1 << n; mask += 1) {
    let sum = 0;
    const picked = [];
    for (let i = 0; i < n; i += 1) {
      if (mask & (1 << i)) {
        sum += Number(list[i].stake) || 0;
        picked.push(list[i]);
      }
    }
    if (Math.abs(sum - target) <= 0.5) return picked;
  }
  return null;
}

function sourceRowsForOrder(order, usedEventIds) {
  // 用本地桥接的实际发单时间 + 金额回溯：同一秒发出的源单里，
  // 挑出金额正好等于这条订单的源单（桥接拆单时也只取自己那一份）。
  const ts = Number(order?.ts) * 1000;
  const stake = Number(order?.stake);
  if (!Number.isFinite(ts) || !Number.isFinite(stake)) return [];
  const candidates = (myEvents().pushes || []).filter((row) => {
    const at = Date.parse(row.signalAt || "");
    if (!Number.isFinite(at)) return false;
    if (Math.abs(at - ts) > 2000) return false;
    return !usedEventIds.has(String(row.eventId));
  });
  if (!candidates.length) return [];
  const total = candidates.reduce((sum, row) => sum + (Number(row.stake) || 0), 0);
  if (Math.abs(total - stake) <= 0.5) return candidates;
  const single = candidates.find((row) => Math.abs(Number(row.stake) - stake) <= 0.5);
  if (single) return [single];
  const subset = subsetForAmount(candidates, stake);
  return subset || candidates;
}

function buildSourceTable(rows) {
  const table = document.createElement("table");
  table.className = "follow-source-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["联赛", "主队 vs 客队", "比分", "盘口", "水位", "金额", "订单号", "代理", "会员"]) {
    headRow.append(el("th", "", label));
  }
  head.append(headRow);
  table.append(head);
  const body = document.createElement("tbody");
  const ordered = [...rows].sort((a, b) =>
    String(a.signalAt || "").localeCompare(String(b.signalAt || "")),
  );
  for (const row of ordered) {
    const tr = document.createElement("tr");
    tr.append(
      el("td", "league", row.league || "—"),
      el("td", "teams", String(row.teams || "—").replace(/-vs-/g, " vs ")),
      el("td", "score", row.score || "—"),
      el("td", "market", formatPickWithPeriod(row)),
      el("td", "odds num", formatOdds(row.odds)),
      el("td", "stake num", formatMoney(row.stake)),
      el("td", "order", row.orderId || "—"),
      el("td", "agent", row.agent || "—"),
      el("td", "member", row.member || "—"),
    );
    body.append(tr);
  }
  table.append(body);
  return table;
}

function toggleSourceRows(mainRow, rows) {
  const next = mainRow.nextElementSibling;
  if (next && next.classList.contains("follow-sources")) {
    next.hidden = !next.hidden;
    mainRow.classList.toggle("expanded", !next.hidden);
    return;
  }
  const tr = document.createElement("tr");
  tr.className = "follow-sources";
  const td = document.createElement("td");
  td.className = "follow-sources-cell";
  td.colSpan = 10;
  td.append(buildSourceTable(rows));
  tr.append(td);
  mainRow.after(tr);
  mainRow.classList.add("expanded");
}

function renderPushRows() {
  const body = $("myEventsPushBody");
  if (!body) return;
  const current = myEvents();
  body.replaceChildren();
  // 一条分单一行：订单号就是我们自己生成、推给跟单程序的那个号。
  const orders = followOrderRows();
  const usedEventIds = new Set();
  for (const order of orders) {
    const follow = followSummary([order]);
    const opponent = `${order.display_line || order.line || "—"}${order.odds ? ` @${order.odds}` : ""}`;
    const tr = document.createElement("tr");
    const resultCell = cell(follow.label, `follow-result ${follow.tone}`);
    resultCell.title = follow.detail;
    const timeCell = cell(formatPushTime(Number(order.ts) * 1000, current.tz), "time");
    const sources = sourceRowsForOrder(order, usedEventIds);
    if (sources.length) {
      sources.forEach((row) => usedEventIds.add(String(row.eventId)));
      tr.className = "follow-row";
      tr.title = "点击查看这笔订单合并的平博源单";
      timeCell.prepend(el("span", "follow-caret", "▸"));
      tr.addEventListener("click", () => toggleSourceRows(tr, sources));
    }
    // 只显示跟单程序成功跟出去的金额。
    const followStakeText = follow.totalStake ? formatMoney(follow.successStake) : "—";
    const followStakeCell = cell(followStakeText, "follow-stake num");
    followStakeCell.title = follow.detail;
    tr.append(
      timeCell,
      cell(order.league || "—", "league"),
      cell(order.sides || "—", "teams"),
      cell(formatFollowBetType(order), "market"),
      cell(opponent, "opponent"),
      cell(formatMoney(order.stake), "stake num"),
      followStakeCell,
      cell(order.orderId || "—", "signal-id"),
      resultCell,
    );
    body.append(tr);
  }
  const empty = $("myEventsPushEmpty");
  if (empty) empty.classList.toggle("hidden", orders.length > 0);
  const count = $("myEventsCount");
  if (count) count.textContent = `${orders.length} 条跟单`;
}

let pushRequestId = 0;

export async function refreshMyEventsPushes() {
  const requestId = ++pushRequestId;
  const current = myEvents();
  current.pushLoading = true;
  try {
    // 推送组明细用于展开看合并前的平博源单。
    try {
      const { response, payload } = await fetchJson(
        `/api/my-events/pushes?${pushQuery()}`,
        { cache: "no-store" },
        { timeoutMs: 10000, allowInvalidJson: true },
      );
      if (response.ok && payload.ok !== false && payload.ready !== false) {
        current.pushes = Array.isArray(payload.rows) ? payload.rows : [];
      }
    } catch (_error) {
      // 源单明细读不到时仍然显示跟单列表，只是不能展开。
    }
    // 跟单视角直接读 HD 的分单（我们自己生成并推给跟单程序）和跟单结果。
    await loadFollowContext();
    if (requestId !== pushRequestId) return;
    current.pushTotal = hdFollowOrders.length;
    renderPushRows();
  } finally {
    if (requestId === pushRequestId) current.pushLoading = false;
  }
}

export async function refreshMyEventsAll(options = {}) {
  if (myEvents().view === "fixtures") {
    await Promise.all([refreshFixtures(options), refreshMyEventsSummary()]);
    return;
  }
  if (myEvents().view === "pushes") {
    await Promise.all([refreshMyEventsPushes(), refreshMyEventsSummary()]);
    return;
  }
  await Promise.all([refreshMyEvents(options), refreshMyEventsSummary()]);
}

export async function loadMoreMyEvents() {
  const current = myEvents();
  if (current.view === "pushes") return;
  if (current.view === "fixtures") {
    if (current.fixtureLoading) return;
    current.fixtureOffset = current.fixtures.length;
    await refreshFixtures();
    return;
  }
  if (current.loading) return;
  current.offset = current.rows.length;
  await refreshMyEvents();
}

export async function pollMyEventsEvents() {
  const current = myEvents();
  const url = current.lastCursor == null
    ? "/api/my-events/events"
    : `/api/my-events/events?cursor=${current.lastCursor}`;
  try {
    const { response, payload } = await fetchJson(
      url,
      { cache: "no-store" },
      { timeoutMs: 8000, allowInvalidJson: true },
    );
    if (!response.ok || payload.ok === false) return;
    if (payload.ready === false) {
      setStatus("采集器未启动");
      return;
    }
    current.lastCursor = payload.cursor ?? current.lastCursor;
    if (Array.isArray(payload.events) && payload.events.length) {
      showMyEventsBanner(payload.events);
      if (current.view === "fixtures") {
        // Refresh the live fixture list at most every 5 seconds; keep the
        // expanded fixture open and refresh its detail in the background.
        await refreshMyEventsSummary();
        const now = Date.now();
        if (!current.fixtureLoading && now - (current.fixtureLastRefreshAt || 0) >= 3000) {
          await refreshFixtures({ reset: true, silent: true, keepOpen: true });
        }
      } else if (current.view === "pushes") {
        await refreshMyEventsSummary();
        await refreshMyEventsPushes();
      } else {
        await refreshMyEvents({ reset: true, silent: true });
        await refreshMyEventsSummary();
      }
    }
  } catch (_error) {
    // A transient local read error must never stop the event cursor loop.
  }
}
