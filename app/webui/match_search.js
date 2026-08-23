(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.HDMatchSearch = factory();
  }
})(typeof window !== "undefined" ? window : globalThis, function () {
  const MODE_LABELS = {
    live: "滚球",
    today: "今日",
    early: "早盘",
    all: "全部",
  };

  function compactText(value) {
    return String(value == null ? "" : value).replace(/\s+/g, " ").trim();
  }

  function normalizeSearchText(value) {
    return compactText(value).toLowerCase().replace(/\s+/g, "");
  }

  function numericIds(value) {
    return compactText(value).match(/\d{4,}/g) || [];
  }

  function orderTeamNames(order, raw) {
    const sourceOrder = order && typeof order === "object" ? order : {};
    const sourceRaw = raw && typeof raw === "object" ? raw : {};
    let homeTeam = compactText(sourceRaw.TEAM_H || sourceRaw.team_h || sourceOrder.homeTeam || sourceOrder.teamH);
    let awayTeam = compactText(sourceRaw.TEAM_C || sourceRaw.team_c || sourceOrder.awayTeam || sourceOrder.teamC);
    if (homeTeam && awayTeam) return [homeTeam, awayTeam];

    const sides = compactText(sourceOrder.sides || sourceOrder.match || sourceRaw.SHOWTEXT_TEAM || "")
      .replace(/<[^>]*>/g, " ")
      .replace(/\(\s*\d+\s*[-:：]\s*\d+\s*\)\s*$/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    const parts = sides.split(/\s+(?:v|vs)\s+/i).map(compactText).filter(Boolean);
    if (!homeTeam && parts.length >= 2) homeTeam = parts[0];
    if (!awayTeam && parts.length >= 2) awayTeam = parts.slice(1).join(" v ");
    return [homeTeam, awayTeam].filter(Boolean);
  }

  function buildHgOrderSearchContext(orders, query) {
    const sourceQuery = compactText(query);
    const queryIds = Array.from(new Set(numericIds(sourceQuery)));
    const rows = Array.isArray(orders) ? orders : [];
    if (!queryIds.length) {
      return { query: sourceQuery, teamIds: [], matchTerms: [], matchedOrderId: "" };
    }

    for (const order of rows) {
      const raw = order && order.raw && typeof order.raw === "object" ? order.raw : {};
      const explicitIds = [raw.NUM_H || raw.num_h, raw.NUM_C || raw.num_c].map(compactText).filter(Boolean);
      const teamIds = Array.from(new Set(explicitIds.concat(numericIds(raw.SHOWTEXT_TEAM_NUM || raw.TNAME || ""))));
      if (!teamIds.length || !queryIds.every((id) => teamIds.includes(id))) continue;

      const [homeTeam = "", awayTeam = ""] = orderTeamNames(order, raw);
      const orderId = compactText(order && (order.orderId || order.order_id) || raw.TID || raw.ORDER_ID);
      return {
        query: sourceQuery,
        teamIds: teamIds.slice(0, 2),
        matchTerms: [homeTeam, awayTeam].filter(Boolean),
        matchedOrderId: orderId,
      };
    }

    return { query: sourceQuery, teamIds: queryIds, matchTerms: [], matchedOrderId: "" };
  }

  function modeLabel(mode) {
    const key = compactText(mode || "live").toLowerCase();
    return MODE_LABELS[key] || compactText(mode) || "滚球";
  }

  function createReadLaneState() {
    return {
      games: { memberId: "", memberLabel: "" },
      odds: { memberId: "", memberLabel: "" },
      events: { memberId: "", memberLabel: "" },
    };
  }

  function setReadLaneMember(state, lane, member) {
    const target = state && typeof state === "object" ? state : createReadLaneState();
    const key = compactText(lane || "games").toLowerCase() || "games";
    if (!target[key]) target[key] = { memberId: "", memberLabel: "" };
    target[key].memberId = compactText(member && (member.id || member.memberId));
    target[key].memberLabel = compactText(member && (member.label || member.memberLabel || member.username || member.id));
    return target;
  }

  function collectReadLaneExcludes(state, lane) {
    const source = state && typeof state === "object" ? state : createReadLaneState();
    const current = compactText(lane || "games").toLowerCase() || "games";
    return Object.keys(source)
      .filter((key) => key !== current)
      .map((key) => compactText(source[key] && source[key].memberId))
      .filter(Boolean);
  }

  function resetReadLaneState(state, lane) {
    const target = state && typeof state === "object" ? state : createReadLaneState();
    if (!lane) {
      Object.keys(target).forEach((key) => {
        target[key] = { memberId: "", memberLabel: "" };
      });
      return target;
    }
    const key = compactText(lane).toLowerCase() || "games";
    target[key] = { memberId: "", memberLabel: "" };
    return target;
  }

  function normalizeScore(score) {
    const text = compactText(score || "");
    if (!text) return "0 : 0";
    const m = text.match(/^(\S+)\s*[:：-]\s*(\S+)$/);
    if (m) return `${m[1]} : ${m[2]}`;
    return text;
  }

  function padClock(value) {
    return String(Math.max(0, Math.floor(Number(value) || 0))).padStart(2, "0");
  }

  function parseClockSeconds(value) {
    const match = compactText(value).match(/(\d{1,3}):(\d{1,2})/);
    if (!match) return null;
    const minutes = Number(match[1]);
    const seconds = Number(match[2]);
    if (!Number.isFinite(minutes) || !Number.isFinite(seconds)) return null;
    return Math.max(0, Math.floor(minutes * 60 + seconds));
  }

  function formatClockSeconds(totalSeconds) {
    const safe = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const minutes = Math.floor(safe / 60);
    const seconds = safe % 60;
    return `${minutes < 10 ? padClock(minutes) : String(minutes)}:${padClock(seconds)}`;
  }

  function normalizePhaseText(value) {
    const text = compactText(value);
    const low = text.toLowerCase();
    if (!text) return "";
    if (text.includes("完场") || text.includes("已结束") || text.includes("结束") || low.includes("ended") || low.includes("finished") || low === "ft") return "已结束";
    if (text.includes("上半场") || low.includes("1st") || low.includes("first half")) return "上半场";
    if (text.includes("下半场") || low.includes("2nd") || low.includes("second half")) return "下半场";
    if (text.includes("中场") || low.includes("half time") || low === "ht") return "中场";
    if (text.includes("未开赛") || low.startsWith("start")) return "未开赛";
    return "";
  }

  function extractClockStatus(sourceText) {
    const text = compactText(sourceText);
    if (!text) return null;
    if (/^(?:FT|完场|已结束)/i.test(text) || normalizePhaseText(text) === "已结束") {
      return { phase: "已结束", seconds: null, running: false, ended: true };
    }
    if (/^(?:HT\^\^|MTIME\^HT)$/i.test(text) || normalizePhaseText(text) === "中场") {
      return { phase: "中场", seconds: null, running: false, ended: false };
    }
    const rawClock = text.match(/\b(1H|2H|ET|Start)\^+([^|\s]+)/i);
    if (rawClock) {
      const key = rawClock[1].toUpperCase();
      const phase = key === "1H" ? "上半场" : key === "2H" ? "下半场" : key === "ET" ? "加时赛" : "未开赛";
      return {
        phase,
        seconds: parseClockSeconds(rawClock[2]),
        running: key === "1H" || key === "2H" || key === "ET",
        ended: false,
      };
    }
    const zhClock = text.match(/(上半场|下半场|加时赛|未开赛)\s*[\|丨]\s*(\d{1,3}:\d{1,2})/);
    if (zhClock) {
      const phase = zhClock[1];
      return {
        phase,
        seconds: parseClockSeconds(zhClock[2]),
        running: phase === "上半场" || phase === "下半场" || phase === "加时赛",
        ended: false,
      };
    }
    const phase = normalizePhaseText(text);
    return phase ? { phase, seconds: null, running: false, ended: phase === "已结束" } : null;
  }

  function eventClockSeconds(event) {
    const src = event && typeof event === "object" ? event : {};
    const seconds = Number(src.seconds);
    const minute = Number(src.minute);
    const injuryTime = Number(src.injuryTime);
    const totalMinutes = (Number.isFinite(minute) ? minute : 0) + (Number.isFinite(injuryTime) ? injuryTime : 0);
    const minuteSeconds = totalMinutes > 0 ? Math.floor(totalMinutes * 60) : null;
    if (Number.isFinite(seconds) && seconds > 0) {
      return minuteSeconds == null ? Math.floor(seconds) : Math.max(Math.floor(seconds), minuteSeconds);
    }
    return minuteSeconds;
  }

  function deriveTimelineClockTime(timeline) {
    const src = timeline && typeof timeline === "object" ? timeline : {};
    const direct = compactText(src.time || src.clock || "");
    if (direct) return direct;
    const phase = normalizePhaseText(src.statusText || src.status || src.statusLine || "");
    if (phase === "已结束") return "FT^";
    if (phase === "中场") return "HT^^";
    const key = phase === "下半场" ? "2H" : phase === "加时赛" ? "ET" : phase === "上半场" ? "1H" : "";
    if (!key) return "";
    let best = null;
    for (const event of Array.isArray(src.events) ? src.events : []) {
      const seconds = eventClockSeconds(event);
      if (seconds != null && (best == null || seconds > best)) best = seconds;
    }
    return best == null ? "" : `${key}^${formatClockSeconds(best)}`;
  }

  function preferFreshestClockTime(primary, fallback) {
    const next = compactText(primary);
    const prev = compactText(fallback);
    if (!next) return prev;
    if (!prev) return next;
    const nextClock = extractClockStatus(next);
    const prevClock = extractClockStatus(prev);
    if (!nextClock) return prev;
    if (nextClock.ended || nextClock.phase === "中场" || nextClock.phase === "未开赛") return next;
    if (!prevClock || prevClock.phase !== nextClock.phase || prevClock.seconds == null) return next;
    if (nextClock.seconds == null) return prev;
    return nextClock.seconds >= prevClock.seconds ? next : prev;
  }

  function resolveMatchStatusDisplay(source = {}, options = {}) {
    const src = source && typeof source === "object" ? source : {};
    const mode = compactText(src.mode || "live").toLowerCase() || "live";
    const rawStatusText = compactText(src.statusText || src.status || "");
    const rawStatusLine = compactText(src.statusLine || "");
    const rawTime = compactText(src.time || src.RETIMESET || src.DATETIME || src.GAME_DATE_TIME || "");
    if (normalizePhaseText(rawStatusText) === "已结束" || normalizePhaseText(rawStatusLine) === "已结束" || /^FT\^/i.test(rawTime)) {
      return { statusLine: "已结束", statusText: "已结束", isEnded: true, isRunningClock: false };
    }
    const candidates = [rawTime, rawStatusLine, rawStatusText].filter(Boolean);
    let clock = null;
    for (const candidate of candidates) {
      clock = extractClockStatus(candidate);
      if (clock) break;
    }
    const now = Number(options.now || Date.now());
    const capturedAt = Number(src.capturedAt || src.lastSeenAt || 0);
    if (clock) {
      if (clock.ended) return { statusLine: "已结束", statusText: "已结束", isEnded: true, isRunningClock: false };
      if (clock.phase === "中场") return { statusLine: "中场", statusText: "中场", isEnded: false, isRunningClock: false };
      if (clock.phase === "未开赛") {
        const start = clock.seconds == null ? rawTime.replace(/^Start\^+/i, "") : formatClockSeconds(clock.seconds);
        return { statusLine: ["未开赛", start].filter(Boolean).join(" | "), statusText: "未开赛", isEnded: false, isRunningClock: false };
      }
      if (clock.seconds != null) {
        const elapsed = clock.running && Number.isFinite(now) && capturedAt > 0 ? Math.max(0, Math.floor((now - capturedAt) / 1000)) : 0;
        return {
          statusLine: `${clock.phase} | ${formatClockSeconds(clock.seconds + elapsed)}`,
          statusText: clock.phase,
          isEnded: false,
          isRunningClock: Boolean(clock.running),
        };
      }
      return { statusLine: clock.phase, statusText: clock.phase, isEnded: false, isRunningClock: false };
    }
    const fallback = rawStatusLine || [modeLabel(mode), rawTime].filter(Boolean).join(" | ") || modeLabel(mode);
    return {
      statusLine: fallback,
      statusText: rawStatusText || fallback,
      isEnded: false,
      isRunningClock: false,
    };
  }

  function matchKeyFrom(game, mode) {
    const gid = compactText(game && (game.gid || game.GID));
    const mtId = compactText(game && (game.mtId || game.MT_ID));
    const home = compactText(game && (game.teamH || game.TEAM_H || game.homeTeam));
    const away = compactText(game && (game.teamC || game.TEAM_C || game.awayTeam));
    const keyBase = gid || mtId || `${home}-${away}`;
    return `${compactText(mode || "live").toLowerCase() || "live"}:${keyBase}`;
  }

  function normalizeHgSearchGame(game, options = {}) {
    const src = game && typeof game === "object" ? game : {};
    const mode = compactText(options.mode || src.mode || "live").toLowerCase() || "live";
    const gid = compactText(src.gid || src.GID);
    const ecid = compactText(src.ecid || src.ECID);
    const mtId = compactText(src.mtId || src.MT_ID);
    const eventId = ecid || mtId || gid || "-";
    const time = compactText(src.time || src.RETIMESET || src.DATETIME || src.GAME_DATE_TIME);
    const capturedAt = Number(src.capturedAt || src.lastSeenAt || 0) || Date.now();
    const status = resolveMatchStatusDisplay({
      mode,
      time,
      statusLine: src.statusLine,
      statusText: src.statusText || src.status,
      capturedAt,
    });
    const retainedAt = Number(src.retainedAt || 0) || 0;

    return {
      key: compactText(src.key) || matchKeyFrom(src, mode),
      provider: compactText(src.provider || "chashui"),
      mode,
      modeLabel: modeLabel(mode),
      memberId: compactText(options.memberId || src.memberId),
      memberLabel: compactText(options.memberLabel || src.memberLabel),
      gid,
      eventId,
      ecid,
      lid: compactText(src.lid || src.LID),
      mtId,
      league: compactText(src.league || src.LEAGUE) || "未命名联赛",
      homeTeam: compactText(src.teamH || src.TEAM_H || src.homeTeam) || "主队",
      awayTeam: compactText(src.teamC || src.TEAM_C || src.awayTeam) || "客队",
      gnumH: compactText(src.gnumH || src.GNUM_H),
      gnumC: compactText(src.gnumC || src.GNUM_C),
      score: normalizeScore(src.score || src.SCORE || ""),
      time,
      statusLine: status.statusLine || modeLabel(mode),
      statusText: status.statusText || compactText(src.statusText || src.status || "") || "可保留",
      isEnded: Boolean(src.isEnded || status.isEnded),
      isRunningClock: Boolean(status.isRunningClock),
      capturedAt,
      moreCount: src.moreCount == null ? null : Number(src.moreCount),
      matchedOrderId: compactText(options.matchedOrderId || src.matchedOrderId),
      teamIds: Array.isArray(options.teamIds || src.teamIds) ? (options.teamIds || src.teamIds).map(compactText).filter(Boolean) : [],
      retainedAt,
      raw: src.raw || src,
    };
  }

  function filterHgSearchGames(games, query, options = {}) {
    const q = normalizeSearchText(query);
    const rows = Array.isArray(games) ? games : [];
    const matchTerms = (Array.isArray(options.matchTerms) ? options.matchTerms : []).map(normalizeSearchText).filter(Boolean);
    if (!q && !matchTerms.length) return rows.slice();
    return rows.filter((game) => {
      const haystack = normalizeSearchText([
        game && (game.gid || game.GID),
        game && (game.ecid || game.ECID),
        game && (game.mtId || game.MT_ID),
        game && (game.league || game.LEAGUE),
        game && (game.teamH || game.TEAM_H || game.homeTeam),
        game && (game.teamC || game.TEAM_C || game.awayTeam),
        game && (game.gnumH || game.GNUM_H),
        game && (game.gnumC || game.GNUM_C),
        game && (game.score || game.SCORE),
        game && (game.time || game.RETIMESET || game.DATETIME || game.GAME_DATE_TIME),
      ].filter(Boolean).join(" "));
      return matchTerms.length ? matchTerms.every((term) => haystack.includes(term)) : haystack.includes(q);
    });
  }

  function collectHgSearchSettledResults(settledResults) {
    const rows = [];
    const seen = new Set();
    let firstError = "";
    let successfulModes = 0;

    (Array.isArray(settledResults) ? settledResults : []).forEach((item) => {
      if (item && item.status === "fulfilled") {
        successfulModes += 1;
        (Array.isArray(item.value) ? item.value : []).forEach((row) => {
          if (!row || !row.key || seen.has(row.key)) return;
          seen.add(row.key);
          rows.push(row);
        });
        return;
      }
      const reason = item && item.reason;
      firstError = firstError || (reason && reason.message ? reason.message : compactText(reason));
    });

    return {
      rows,
      error: successfulModes ? "" : firstError,
      warning: successfulModes && firstError ? "部分比赛分类暂不可用，已展示其余分类的查询结果。" : "",
    };
  }

  function upsertRetainedHgMatch(retained, match) {
    const rows = Array.isArray(retained) ? retained.slice() : [];
    const normalized = normalizeHgSearchGame(match, {
      mode: match && match.mode,
      memberId: match && match.memberId,
      memberLabel: match && match.memberLabel,
    });
    normalized.retainedAt = Number(match && match.retainedAt) || Date.now();
    const idx = rows.findIndex((row) => row && (row.key === normalized.key || (row.gid && row.gid === normalized.gid && row.mode === normalized.mode)));
    if (idx >= 0) {
      rows[idx] = Object.assign({}, rows[idx], normalized, { retainedAt: Number(rows[idx].retainedAt || 0) || normalized.retainedAt });
      return rows;
    }
    return [normalized].concat(rows);
  }

  function removeRetainedHgMatch(retained, key) {
    const wanted = compactText(key);
    return (Array.isArray(retained) ? retained : []).filter((row) => row && row.key !== wanted);
  }

  function mergeRetainedHgMatches(retained, updates) {
    const updateMap = new Map();
    for (const update of Array.isArray(updates) ? updates : []) {
      if (!update || typeof update !== "object") continue;
      const normalized = normalizeHgSearchGame(update, { mode: update.mode, memberId: update.memberId, memberLabel: update.memberLabel });
      updateMap.set(normalized.key, normalized);
    }
    return (Array.isArray(retained) ? retained : []).map((row) => {
      if (!row || typeof row !== "object") return row;
      const hit = updateMap.get(row.key);
      return hit ? Object.assign({}, row, hit, { retainedAt: Number(row.retainedAt || 0) || hit.retainedAt || 0 }) : row;
    });
  }

  function isHgMatchRetained(retained, matchOrKey) {
    const key = typeof matchOrKey === "string" ? matchOrKey : normalizeHgSearchGame(matchOrKey || {}).key;
    return (Array.isArray(retained) ? retained : []).some((row) => row && row.key === key);
  }

  function buildHgEventDetailItems(match) {
    const model = normalizeHgSearchGame(match || {}, match || {});
    return [
      { label: "事件ID", value: compactText(model.ecid || model.eventId || model.gid) || "-" },
      { label: "数据ID", value: compactText(model.mtId || model.eventMatchId || (model.raw && (model.raw.MT_ID || model.raw.mtId))) || "-" },
      { label: "当前比分", value: normalizeScore(model.score) },
      { label: "比赛状态", value: compactText(model.statusLine || [model.modeLabel, model.time].filter(Boolean).join(" | ")) || "-" },
      { label: "主队", value: compactText(model.homeTeam) || "-" },
      { label: "客队", value: compactText(model.awayTeam) || "-" },
    ];
  }

  function applyHgEventSnapshotToMatch(match, snapshot) {
    const model = normalizeHgSearchGame(match || {}, match || {});
    const source = snapshot && typeof snapshot === "object" ? snapshot : {};
    const timeline = source.timeline && typeof source.timeline === "object" ? source.timeline : {};
    const meta = source.meta && typeof source.meta === "object" ? source.meta : {};
    const mode = compactText(meta.mode || model.mode || "live").toLowerCase() || "live";
    const capturedAt = Number(timeline.capturedAt || source.capturedAt || Date.now()) || Date.now();
    const timelineTime = deriveTimelineClockTime(timeline);
    const time = preferFreshestClockTime(timelineTime, model.time || model.statusLine);
    const status = resolveMatchStatusDisplay({
      mode,
      time,
      statusLine: timeline.statusLine || model.statusLine,
      statusText: timeline.statusText || model.statusText,
      capturedAt,
    });
    return Object.assign({}, model, {
      mode,
      modeLabel: modeLabel(mode),
      gid: compactText(meta.masterGid || meta.requestedGid || model.gid),
      mtId: compactText(timeline.matchId || meta.mtId || model.mtId),
      homeTeam: compactText(timeline.homeTeam || model.homeTeam) || "主队",
      awayTeam: compactText(timeline.awayTeam || model.awayTeam) || "客队",
      score: normalizeScore(timeline.score || model.score),
      time,
      statusText: status.statusText || "实时更新",
      statusLine: status.statusLine || compactText(model.statusLine || [modeLabel(mode), model.time].filter(Boolean).join(" | ")) || modeLabel(mode),
      isEnded: Boolean(status.isEnded),
      isRunningClock: Boolean(status.isRunningClock),
      capturedAt,
      raw: model.raw,
    });
  }

  function eventComparableSeconds(event) {
    const src = event && typeof event === "object" ? event : {};
    const seconds = Number(src.seconds);
    if (Number.isFinite(seconds) && seconds > 0) return seconds;
    const minute = Number(src.minute);
    const injuryTime = Number(src.injuryTime);
    return Math.max(0, (Number.isFinite(minute) ? minute : 0) * 60 + (Number.isFinite(injuryTime) ? injuryTime : 0) * 60);
  }

  function sortTimelineEvents(events) {
    return (Array.isArray(events) ? events : []).slice().sort((left, right) => {
      const lm = Number(left && left.minute);
      const rm = Number(right && right.minute);
      const leftMinute = Number.isFinite(lm) ? lm : -1;
      const rightMinute = Number.isFinite(rm) ? rm : -1;
      if (leftMinute !== rightMinute) return rightMinute - leftMinute;
      const li = Number(left && left.injuryTime);
      const ri = Number(right && right.injuryTime);
      const leftInjury = Number.isFinite(li) ? li : 0;
      const rightInjury = Number.isFinite(ri) ? ri : 0;
      if (leftInjury !== rightInjury) return rightInjury - leftInjury;
      return eventComparableSeconds(right) - eventComparableSeconds(left);
    });
  }

  function mergeHgEventTimeline(previous, incoming) {
    const prev = previous && typeof previous === "object" ? previous : {};
    const next = incoming && typeof incoming === "object" ? incoming : {};
    const merged = new Map();

    for (const event of (Array.isArray(prev.events) ? prev.events : [])) {
      if (!event || typeof event !== "object") continue;
      const id = compactText(event.id);
      if (id) merged.set(id, event);
    }
    for (const event of (Array.isArray(next.events) ? next.events : [])) {
      if (!event || typeof event !== "object") continue;
      const id = compactText(event.id);
      if (id) merged.set(id, Object.assign({}, merged.get(id) || {}, event));
    }

    return Object.assign({}, prev, next, {
      events: sortTimelineEvents(Array.from(merged.values())),
      capturedAt: next.capturedAt || Date.now(),
    });
  }

  function formatHgEventErrorMessage(message) {
    const text = compactText(message);
    if (!text) return "比赛事件读取失败，请稍后重试。";
    if (text.includes("missing_betradar_feed_token")) {
      return "当前缺少比赛事件源授权，请先采集或配置 BETRADAR_FEED_TOKEN。";
    }
    if (text.includes("missing_match_id")) {
      return "当前比赛没有可用的事件流编号，暂时无法读取比赛事件。";
    }
    if (text.includes("missing_member_id")) {
      return "当前没有可用的在线会员会话，无法解析事件流编号。";
    }
    if (text.includes("member_not_found")) {
      return "当前会员会话已失效，请刷新会员状态后重试。";
    }
    if (text.includes("member_missing_uid_cookie_ver")) {
      return "当前会员会话缺少 UID/Cookie/版本信息，无法解析事件流编号。";
    }
    if (text.includes("resolve_match_id_failed: game_not_found")) {
      return "当前赛事不在该会员实时列表中，暂时无法解析事件流编号。";
    }
    if (text.includes("resolve_match_id_failed")) {
      return "事件流编号解析失败，请刷新比赛列表后重试。";
    }
    if (text.includes("Unauthorized") || text.includes("403")) {
      return "比赛事件源授权已失效，请重新采集 BETRADAR_FEED_TOKEN。";
    }
    return text;
  }

  function oddsPeriodLabel(wtype) {
    return compactText(wtype).toUpperCase().startsWith("H") ? "上半场" : "全场";
  }

  function addOddsEntry(entries, label, odd) {
    const entryLabel = compactText(label) || "-";
    const entryOdd = compactText(odd) || "-";
    if (entryLabel === "-" && entryOdd === "-") return;
    entries.push({ label: entryLabel, odd: entryOdd });
  }

  function oddsOptionLabel(option, fallbackLabel) {
    const src = option && typeof option === "object" ? option : {};
    return [compactText(src.team || src.label || fallbackLabel), compactText(src.line)].filter(Boolean).join(" ");
  }

  function buildHgOddsSections(snapshot) {
    const source = snapshot && typeof snapshot === "object" ? snapshot : {};
    const simple = source.simple && typeof source.simple === "object" ? source.simple : source;
    const groups = new Map();

    function groupEntries(label) {
      const key = compactText(label) || "盘口";
      if (!groups.has(key)) groups.set(key, []);
      return groups.get(key);
    }

    const handicapLines = simple.handicap && Array.isArray(simple.handicap.lines) ? simple.handicap.lines : [];
    handicapLines.forEach((line) => {
      const row = line && typeof line === "object" ? line : {};
      const entries = groupEntries(`${oddsPeriodLabel(row.wtype)}让球`);
      addOddsEntry(entries, oddsOptionLabel(row.home, "主队"), row.home && row.home.odd);
      addOddsEntry(entries, oddsOptionLabel(row.away, "客队"), row.away && row.away.odd);
    });

    const overUnderLines = simple.overUnder && Array.isArray(simple.overUnder.lines) ? simple.overUnder.lines : [];
    overUnderLines.forEach((line) => {
      const row = line && typeof line === "object" ? line : {};
      const entries = groupEntries(`${oddsPeriodLabel(row.wtype)}大小`);
      addOddsEntry(entries, oddsOptionLabel(row.over, "大"), row.over && row.over.odd);
      addOddsEntry(entries, oddsOptionLabel(row.under, "小"), row.under && row.under.odd);
    });

    const moneyLine = simple.moneyLine && typeof simple.moneyLine === "object" ? simple.moneyLine : null;
    if (moneyLine && !moneyLine.empty) {
      const entries = groupEntries(`${compactText(moneyLine.wtype).toUpperCase().startsWith("H") ? "上半场" : ""}独赢`);
      addOddsEntry(entries, oddsOptionLabel(moneyLine.home, "主队"), moneyLine.home && moneyLine.home.odd);
      addOddsEntry(entries, oddsOptionLabel(moneyLine.draw, "和局"), moneyLine.draw && moneyLine.draw.odd);
      addOddsEntry(entries, oddsOptionLabel(moneyLine.away, "客队"), moneyLine.away && moneyLine.away.odd);
    }

    const order = ["全场让球", "上半场让球", "全场大小", "上半场大小", "独赢", "上半场独赢"];
    return order
      .filter((label) => groups.has(label))
      .concat(Array.from(groups.keys()).filter((label) => !order.includes(label)))
      .map((label) => ({ label, entries: groups.get(label) || [] }))
      .filter((section) => section.entries.length > 0);
  }

  return {
    MODE_LABELS,
    applyHgEventSnapshotToMatch,
    buildHgEventDetailItems,
    buildHgOddsSections,
    buildHgOrderSearchContext,
    collectReadLaneExcludes,
    collectHgSearchSettledResults,
    compactText,
    createReadLaneState,
    filterHgSearchGames,
    formatHgEventErrorMessage,
    isHgMatchRetained,
    mergeHgEventTimeline,
    mergeRetainedHgMatches,
    modeLabel,
    normalizeHgSearchGame,
    normalizeScore,
    resetReadLaneState,
    removeRetainedHgMatch,
    resolveMatchStatusDisplay,
    setReadLaneMember,
    upsertRetainedHgMatch,
  };
});
