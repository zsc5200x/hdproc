(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.HDOrderRealtimeBridge = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const MODE_LABELS = {
    live: '滚球',
    today: '今日',
    early: '早盘',
  };

  function compactText(value) {
    return String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  }

  function compareText(value) {
    return compactText(value).toLowerCase().replace(/\s+/g, '');
  }

  function parseOrderTeams(order) {
    const raw = order && order.raw && typeof order.raw === 'object' ? order.raw : {};
    let homeTeam = compactText(raw.TEAM_H || raw.team_h || order && (order.homeTeam || order.teamH));
    let awayTeam = compactText(raw.TEAM_C || raw.team_c || order && (order.awayTeam || order.teamC));
    if (!homeTeam || !awayTeam) {
      const sides = compactText(order && (order.sides || order.match_name || order.matchName)).replace(/\s*[（(][^()（）]*[）)]\s*$/, '');
      const parts = sides.split(/\s+(?:v|vs|VS|V)\s+/).map(compactText).filter(Boolean);
      if (parts.length >= 2) {
        homeTeam = homeTeam || parts[0];
        awayTeam = awayTeam || parts.slice(1).join(' v ');
      }
    }
    return { homeTeam, awayTeam };
  }

  function matchSignature(league, homeTeam, awayTeam) {
    const parts = [compareText(league), compareText(homeTeam), compareText(awayTeam)];
    return parts.every(Boolean) ? parts.join('|') : '';
  }

  function orderMatchSignature(order) {
    const teams = parseOrderTeams(order);
    return matchSignature(order && order.league, teams.homeTeam, teams.awayTeam);
  }

  function isLiveOrder(order) {
    const raw = order && order.raw && typeof order.raw === 'object' ? order.raw : {};
    const modeText = compactText(
      order && (
        order.orderType ||
        order.order_type ||
        raw.orderType ||
        raw.ORDER_TYPE ||
        raw.MARKET ||
        raw.market
      ),
    );
    return normalizeMode(modeText) === 'live';
  }

  function liveMatchSignature(live) {
    return matchSignature(live && live.league, live && live.homeTeam, live && live.awayTeam);
  }

  function normalizeScore(score) {
    const text = compactText(score || '');
    if (!text) return '0 : 0';
    const m = text.match(/^(\S+)\s*[:：-]\s*(\S+)$/);
    if (m) return `${m[1]} : ${m[2]}`;
    return text;
  }

  function normalizeMode(value) {
    const text = compactText(value).toLowerCase();
    if (text === 'live' || text === 'rb' || text.includes('滚球')) return 'live';
    if (text === 'early' || text.includes('早盘')) return 'early';
    if (text === 'today' || text.includes('今日')) return 'today';
    return text || 'today';
  }

  function padClock(value) {
    return String(Math.max(0, Math.floor(Number(value) || 0))).padStart(2, '0');
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
    if (!text) return '';
    if (text.includes('完场') || text.includes('已结束') || text.includes('结束') || low.includes('ended') || low.includes('finished') || low === 'ft') return '已结束';
    if (text.includes('上半场') || low.includes('1st') || low.includes('first half')) return '上半场';
    if (text.includes('下半场') || low.includes('2nd') || low.includes('second half')) return '下半场';
    if (text.includes('中场') || low.includes('half time') || low === 'ht') return '中场';
    if (text.includes('未开赛') || low.startsWith('start')) return '未开赛';
    return '';
  }

  function extractClockStatus(sourceText) {
    const text = compactText(sourceText);
    if (!text) return null;
    if (/^(?:FT|完场|已结束)/i.test(text) || normalizePhaseText(text) === '已结束') {
      return { phase: '已结束', seconds: null, running: false, ended: true };
    }
    if (/^(?:HT\^\^|MTIME\^HT)$/i.test(text) || normalizePhaseText(text) === '中场') {
      return { phase: '中场', seconds: null, running: false, ended: false };
    }
    const rawClock = text.match(/\b(1H|2H|ET|Start)\^+([^|\s]+)/i);
    if (rawClock) {
      const key = rawClock[1].toUpperCase();
      const phase = key === '1H' ? '上半场' : key === '2H' ? '下半场' : key === 'ET' ? '加时赛' : '未开赛';
      return {
        phase,
        seconds: parseClockSeconds(rawClock[2]),
        running: key === '1H' || key === '2H' || key === 'ET',
        ended: false,
      };
    }
    const zhClock = text.match(/(上半场|下半场|加时赛|未开赛)\s*[\|丨]\s*(\d{1,3}:\d{1,2})/);
    if (zhClock) {
      const phase = zhClock[1];
      return {
        phase,
        seconds: parseClockSeconds(zhClock[2]),
        running: phase === '上半场' || phase === '下半场' || phase === '加时赛',
        ended: false,
      };
    }
    const phase = normalizePhaseText(text);
    return phase ? { phase, seconds: null, running: false, ended: phase === '已结束' } : null;
  }

  function eventClockSeconds(event) {
    const src = event && typeof event === 'object' ? event : {};
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
    const src = timeline && typeof timeline === 'object' ? timeline : {};
    const direct = compactText(src.time || src.clock || '');
    if (direct) return direct;
    const phase = normalizePhaseText(src.statusText || src.status || src.statusLine || '');
    if (phase === '已结束') return 'FT^';
    if (phase === '中场') return 'HT^^';
    const key = phase === '下半场' ? '2H' : phase === '加时赛' ? 'ET' : phase === '上半场' ? '1H' : '';
    if (!key) return '';
    let best = null;
    for (const event of Array.isArray(src.events) ? src.events : []) {
      const seconds = eventClockSeconds(event);
      if (seconds != null && (best == null || seconds > best)) best = seconds;
    }
    return best == null ? '' : `${key}^${formatClockSeconds(best)}`;
  }

  function preferFreshestClockTime(primary, fallback) {
    const next = compactText(primary);
    const prev = compactText(fallback);
    if (!next) return prev;
    if (!prev) return next;
    const nextClock = extractClockStatus(next);
    const prevClock = extractClockStatus(prev);
    if (!nextClock) return prev;
    if (nextClock.ended || nextClock.phase === '中场' || nextClock.phase === '未开赛') return next;
    if (!prevClock || prevClock.phase !== nextClock.phase || prevClock.seconds == null) return next;
    if (nextClock.seconds == null) return prev;
    return nextClock.seconds >= prevClock.seconds ? next : prev;
  }

  function resolveMatchStatusDisplay(source = {}, options = {}) {
    const src = source && typeof source === 'object' ? source : {};
    const mode = normalizeMode(src.mode || 'live');
    const rawStatusText = compactText(src.statusText || src.status || '');
    const rawStatusLine = compactText(src.statusLine || '');
    const rawTime = compactText(src.time || src.RETIMESET || src.DATETIME || src.GAME_DATE_TIME || '');
    if (normalizePhaseText(rawStatusText) === '已结束' || normalizePhaseText(rawStatusLine) === '已结束' || /^FT\^/i.test(rawTime)) {
      return { statusLine: '已结束', statusText: '已结束', isEnded: true, isRunningClock: false };
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
      if (clock.ended) return { statusLine: '已结束', statusText: '已结束', isEnded: true, isRunningClock: false };
      if (clock.phase === '中场') return { statusLine: '中场', statusText: '中场', isEnded: false, isRunningClock: false };
      if (clock.phase === '未开赛') {
        const start = clock.seconds == null ? rawTime.replace(/^Start\^+/i, '') : formatClockSeconds(clock.seconds);
        return { statusLine: ['未开赛', start].filter(Boolean).join(' | '), statusText: '未开赛', isEnded: false, isRunningClock: false };
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
    const fallback = rawStatusLine || [modeLabel(mode), rawTime].filter(Boolean).join(' | ') || modeLabel(mode);
    return {
      statusLine: fallback,
      statusText: rawStatusText || fallback,
      isEnded: false,
      isRunningClock: false,
    };
  }

  function modeLabel(mode) {
    return MODE_LABELS[normalizeMode(mode)] || compactText(mode) || '今日';
  }

  function orderTypeFromMode(mode) {
    return normalizeMode(mode) === 'live' ? '滚球' : '早盘';
  }

  function extractOrderId(order) {
    const raw = order && order.raw && typeof order.raw === 'object' ? order.raw : {};
    return compactText(order && (order.orderId || order.order_id || raw.orderId || raw.order_id || raw.ORDER_ID || raw.TID));
  }

  function extractOrderTarget(order) {
    return compactText(order && order.target);
  }

  function extractOrderGid(order) {
    const raw = order && order.raw && typeof order.raw === 'object' ? order.raw : {};
    return compactText(order && (order.gid || order.eventId || order.event_id || raw._EVENT || raw.EVENT || raw.GID || raw.gid));
  }

  function displayKey(order) {
    const target = extractOrderTarget(order);
    const oid = extractOrderId(order);
    if (target || oid) return `${target}|${oid}`;
    return extractOrderGid(order);
  }

  function queueStatusLabel(status) {
    const st = compactText(status).toLowerCase();
    if (st === 'job') return '队列中';
    if (st === 'pending') return '待入队';
    return compactText(status) || '待入队';
  }

  function normalizeQueueOrder(item) {
    const queueItem = item && typeof item === 'object' ? item : {};
    const order = queueItem.order && typeof queueItem.order === 'object' ? queueItem.order : {};
    const derived = order.derived && typeof order.derived === 'object' ? order.derived : {};
    const gid = compactText(order.gid || derived.gid);
    const mode = normalizeMode(derived.mode || order.mode || order.market || '');
    const label = queueStatusLabel(queueItem.status);
    const raw = Object.assign({}, order.raw && typeof order.raw === 'object' ? order.raw : {}, {
      _EVENT: gid,
      MARKET: mode === 'live' ? 'RB' : compactText(order.market || ''),
    });
    return {
      target: compactText(order.target) || null,
      orderId: extractOrderId(order) || null,
      ts: order.ts,
      bet_ts: order.bet_ts,
      sport: compactText(order.sport) || null,
      stake: compactText(order.stake) || null,
      odds: compactText(order.odds) || null,
      league: compactText(order.league) || null,
      sides: compactText(order.sides) || null,
      betType: compactText(order.betType) || null,
      line: compactText(order.line) || null,
      gid: gid || null,
      orderType: orderTypeFromMode(mode),
      status: label,
      status_label: label,
      raw,
      _follow_queue: {
        key: compactText(queueItem.key),
        status: compactText(queueItem.status),
        label,
        job: queueItem.job || null,
        derived,
      },
    };
  }

  function mergeQueueMeta(order, queueOrder) {
    const raw = Object.assign(
      {},
      order.raw && typeof order.raw === 'object' ? order.raw : {},
      queueOrder.raw && typeof queueOrder.raw === 'object' ? queueOrder.raw : {},
    );
    return Object.assign({}, order, {
      raw,
      gid: extractOrderGid(order) || queueOrder.gid || undefined,
      orderType: queueOrder.orderType || order.orderType,
      status: queueOrder.status || order.status,
      status_label: queueOrder.status_label || order.status_label,
      _follow_queue: queueOrder._follow_queue,
    });
  }

  function orderTs(order) {
    const n = Number(order && order.ts);
    return Number.isFinite(n) ? n : 0;
  }

  function isUsefulValue(value) {
    return value !== null && value !== undefined && value !== '';
  }

  function orderDetailScore(order) {
    const row = order && typeof order === 'object' ? order : {};
    const raw = row.raw && typeof row.raw === 'object' ? row.raw : {};
    let score = 0;
    if (row._follow_queue) score += 1000;
    if (row._live_match) score += 500;
    if (row.display_line) score += 80;
    score += Object.keys(raw).length;
    score += Object.keys(row).filter((key) => isUsefulValue(row[key])).length;
    return score;
  }

  function preferRealtimeOrderRow(a, b) {
    const aQueue = Boolean(a && a._follow_queue);
    const bQueue = Boolean(b && b._follow_queue);
    if (aQueue !== bQueue) return bQueue ? b : a;
    const aTs = orderTs(a);
    const bTs = orderTs(b);
    if (aTs !== bTs) return bTs > aTs ? b : a;
    return orderDetailScore(b) > orderDetailScore(a) ? b : a;
  }

  function mergeDuplicateRealtimeOrder(existing, candidate) {
    const primary = preferRealtimeOrderRow(existing, candidate);
    const secondary = primary === existing ? candidate : existing;
    const raw = Object.assign(
      {},
      secondary && secondary.raw && typeof secondary.raw === 'object' ? secondary.raw : {},
      primary && primary.raw && typeof primary.raw === 'object' ? primary.raw : {},
    );
    const merged = Object.assign({}, secondary, primary, { raw });
    Object.keys(primary || {}).forEach((key) => {
      if (!isUsefulValue(primary[key]) && isUsefulValue(secondary && secondary[key])) {
        merged[key] = secondary[key];
      }
    });
    if (!merged._follow_queue && secondary && secondary._follow_queue) merged._follow_queue = secondary._follow_queue;
    if (!merged._live_match && secondary && secondary._live_match) merged._live_match = secondary._live_match;
    if (!merged._live_match_lookup && secondary && secondary._live_match_lookup) merged._live_match_lookup = secondary._live_match_lookup;
    return merged;
  }

  function dedupeRealtimeOrdersByOrderId(rows) {
    const deduped = [];
    const indexByOrderId = new Map();
    for (const row of Array.isArray(rows) ? rows : []) {
      const oid = extractOrderId(row);
      if (!oid) {
        deduped.push(row);
        continue;
      }
      if (!indexByOrderId.has(oid)) {
        indexByOrderId.set(oid, deduped.length);
        deduped.push(row);
        continue;
      }
      const idx = indexByOrderId.get(oid);
      deduped[idx] = mergeDuplicateRealtimeOrder(deduped[idx], row);
    }
    return deduped;
  }

  function mergeFollowQueueOrders(orders, queueItems) {
    const rows = (Array.isArray(orders) ? orders : []).map((row) => {
      const raw = row && row.raw && typeof row.raw === 'object' ? Object.assign({}, row.raw) : row && row.raw;
      return Object.assign({}, row, raw && typeof raw === 'object' ? { raw } : {});
    });
    const index = new Map();
    rows.forEach((row, idx) => {
      const key = displayKey(row);
      if (key) index.set(key, idx);
    });

    for (const item of Array.isArray(queueItems) ? queueItems : []) {
      const queueOrder = normalizeQueueOrder(item);
      const key = displayKey(queueOrder);
      if (key && index.has(key)) {
        const idx = index.get(key);
        rows[idx] = mergeQueueMeta(rows[idx], queueOrder);
      } else {
        if (key) index.set(key, rows.length);
        rows.push(queueOrder);
      }
    }

    return dedupeRealtimeOrdersByOrderId(rows).sort((a, b) => orderTs(b) - orderTs(a));
  }

  function normalizeLiveMatch(game, options = {}) {
    const src = game && typeof game === 'object' ? game : {};
    const mode = normalizeMode(options.mode || src.mode || 'live');
    const time = compactText(src.time || src.RETIMESET || src.DATETIME || src.GAME_DATE_TIME);
    const capturedAt = Number(src.capturedAt || src.lastSeenAt || 0) || Date.now();
    const status = resolveMatchStatusDisplay({
      mode,
      time,
      statusLine: src.statusLine,
      statusText: src.statusText || src.status,
      capturedAt,
    });
    return {
      key: `${mode}:${compactText(src.gid || src.GID || src.mtId || src.MT_ID)}`,
      provider: 'chashui',
      mode,
      modeLabel: modeLabel(mode),
      memberId: compactText(options.memberId || src.memberId),
      memberLabel: compactText(options.memberLabel || src.memberLabel),
      gid: compactText(src.gid || src.GID),
      eventId: compactText(src.ecid || src.ECID || src.mtId || src.MT_ID || src.gid || src.GID),
      ecid: compactText(src.ecid || src.ECID),
      lid: compactText(src.lid || src.LID),
      mtId: compactText(src.mtId || src.MT_ID),
      league: compactText(src.league || src.LEAGUE),
      homeTeam: compactText(src.teamH || src.TEAM_H || src.homeTeam),
      awayTeam: compactText(src.teamC || src.TEAM_C || src.awayTeam),
      score: normalizeScore(src.score || src.SCORE || ''),
      time,
      statusLine: status.statusLine || modeLabel(mode),
      statusText: status.statusText || compactText(src.statusText || src.status || '') || '实时更新',
      isEnded: Boolean(src.isEnded || status.isEnded),
      isRunningClock: Boolean(status.isRunningClock),
      capturedAt,
      raw: src.raw || src,
    };
  }

  function applyLiveMatchSnapshotsToOrders(orders, games, options = {}) {
    const byGid = new Map();
    const bySignature = new Map();
    const checkedAt = Number(options.checkedAt || Date.now()) || Date.now();
    const lookupMode = normalizeMode(options.mode || 'live');
    const lookupBase = {
      mode: lookupMode,
      checkedAt,
      memberId: compactText(options.memberId),
      memberLabel: compactText(options.memberLabel),
    };
    for (const game of Array.isArray(games) ? games : []) {
      const live = normalizeLiveMatch(game, options);
      if (live.gid) byGid.set(live.gid, live);
      const sig = liveMatchSignature(live);
      if (sig && !bySignature.has(sig)) bySignature.set(sig, live);
    }
    return (Array.isArray(orders) ? orders : []).map((order) => {
      const gid = extractOrderGid(order);
      const live = (gid ? byGid.get(gid) : null) || bySignature.get(orderMatchSignature(order));
      if (!live) {
        if (lookupMode === 'live' && isLiveOrder(order)) {
          return Object.assign({}, order, {
            _live_match_lookup: Object.assign({}, lookupBase, {
              matched: false,
              gid,
            }),
          });
        }
        return order;
      }
      const originalEvent = gid && live.gid && gid !== live.gid ? gid : "";
      const raw = Object.assign({}, order.raw && typeof order.raw === 'object' ? order.raw : {}, {
        _EVENT: live.gid || gid,
        ORDER_EVENT: originalEvent || (order.raw && order.raw.ORDER_EVENT) || undefined,
        ECID: live.ecid || undefined,
        LID: live.lid || undefined,
        MT_ID: live.mtId || undefined,
      });
      return Object.assign({}, order, {
        raw,
        _live_match: live,
        _live_match_lookup: Object.assign({}, lookupBase, {
          matched: true,
          gid: live.gid || gid,
        }),
      });
    });
  }

  function normalizeEventSnapshot(entry, options = {}) {
    const source = entry && typeof entry === 'object' ? entry : {};
    const snapshot = source.snapshot && typeof source.snapshot === 'object' ? source.snapshot : source;
    const meta = snapshot.meta && typeof snapshot.meta === 'object' ? snapshot.meta : {};
    const timeline = snapshot.timeline && typeof snapshot.timeline === 'object' ? snapshot.timeline : {};
    const mode = normalizeMode(options.mode || source.mode || meta.mode || 'live');
    const gid = compactText(source.gid || source.orderGid || meta.masterGid || meta.requestedGid);
    const mtId = compactText(source.mtId || timeline.matchId || meta.mtId);
    const score = normalizeScore(timeline.score || source.score || '');
    const time = preferFreshestClockTime(deriveTimelineClockTime(timeline), source.time || source.statusLine || timeline.statusLine);
    const capturedAt = Number(timeline.capturedAt || source.capturedAt || Date.now()) || Date.now();
    const status = resolveMatchStatusDisplay({
      mode,
      time,
      statusLine: timeline.statusLine || source.statusLine,
      statusText: timeline.statusText || source.statusText,
      capturedAt,
    });
    return {
      key: `${mode}:${gid || mtId}`,
      provider: 'betradar-events',
      mode,
      modeLabel: modeLabel(mode),
      memberId: compactText(options.memberId || source.memberId),
      memberLabel: compactText(options.memberLabel || source.memberLabel),
      gid,
      eventId: gid || mtId,
      ecid: compactText(source.ecid || meta.ecid),
      lid: compactText(source.lid || meta.lid),
      mtId,
      league: compactText(source.league || meta.league),
      homeTeam: compactText(timeline.homeTeam || source.homeTeam || source.teamH),
      awayTeam: compactText(timeline.awayTeam || source.awayTeam || source.teamC),
      score,
      time,
      statusLine: status.statusLine || [modeLabel(mode), time].filter(Boolean).join(' | ') || modeLabel(mode),
      statusText: status.statusText || '实时更新',
      isEnded: Boolean(status.isEnded),
      isRunningClock: Boolean(status.isRunningClock),
      capturedAt,
      raw: snapshot,
    };
  }

  function applyEventSnapshotsToOrders(orders, snapshots, options = {}) {
    const byGid = new Map();
    const byMtId = new Map();
    for (const entry of Array.isArray(snapshots) ? snapshots : []) {
      const live = normalizeEventSnapshot(entry, options);
      if (live.gid) byGid.set(live.gid, live);
      if (live.mtId) byMtId.set(live.mtId, live);
    }
    return (Array.isArray(orders) ? orders : []).map((order) => {
      const raw0 = order && order.raw && typeof order.raw === 'object' ? order.raw : {};
      const gid = extractOrderGid(order);
      const mtId = compactText(raw0.MT_ID || raw0.mtId || order && (order.mtId || order.eventMatchId));
      const live = (gid ? byGid.get(gid) : null) || (mtId ? byMtId.get(mtId) : null);
      if (!live) return order;
      const raw = Object.assign({}, raw0, {
        _EVENT: live.gid || gid,
        MT_ID: live.mtId || mtId || undefined,
        ECID: live.ecid || raw0.ECID || undefined,
        LID: live.lid || raw0.LID || undefined,
      });
      return Object.assign({}, order, { raw, _live_match: live });
    });
  }

  return {
    applyEventSnapshotsToOrders,
    applyLiveMatchSnapshotsToOrders,
    compactText,
    dedupeRealtimeOrdersByOrderId,
    displayKey,
    extractOrderGid,
    extractOrderId,
    mergeFollowQueueOrders,
    modeLabel,
    normalizeEventSnapshot,
    normalizeLiveMatch,
    normalizeMode,
    normalizeScore,
    resolveMatchStatusDisplay,
  };
});
