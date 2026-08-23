const { chromium } = require('playwright');

const { parseGameListXml, parseGameMoreXml } = require('./parsers');

const FT_ORDER_VIEW_CACHE_MS = 1800;

function safeOrigin(url) {
  try {
    return new URL(url).origin;
  } catch {
    return 'https://hga027.com';
  }
}

function safeNowIso() {
  return new Date().toISOString();
}

function maskUid(uid) {
  if (!uid) return null;
  if (uid.length <= 8) return '***';
  return `${uid.slice(0, 4)}…${uid.slice(-4)}`;
}

function parseForm(postData) {
  if (!postData) return null;
  try {
    const params = new URLSearchParams(postData);
    return Object.fromEntries(params.entries());
  } catch {
    return null;
  }
}

function extractTag(xmlText, tag) {
  const re = new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`);
  const m = xmlText.match(re);
  return m ? m[1] : null;
}

function parseFtOrderViewXml(xmlText) {
  return {
    code: extractTag(xmlText, 'code'),
    ioratio: extractTag(xmlText, 'ioratio'),
    spread: extractTag(xmlText, 'spread'),
    strong: extractTag(xmlText, 'strong'),
    fastCheck: extractTag(xmlText, 'fast_check')
  };
}

function normalizeLine(value) {
  return String(value || '')
    .trim()
    .replace(/\s+/g, '')
    .replace(/^\+/, '');
}

function isPositiveOdd(value) {
  const n = Number.parseFloat(String(value || ''));
  return Number.isFinite(n) && n > 0;
}

function lineAverage(value) {
  const s = normalizeLine(value).replace(/^[+-]/, '');
  if (!s) return Number.NaN;
  const parts = s.split('/').filter(Boolean);
  if (parts.length === 0) return Number.NaN;
  const nums = parts.map((p) => Number.parseFloat(p));
  if (nums.some((n) => !Number.isFinite(n))) return Number.NaN;
  return nums.reduce((a, b) => a + b, 0) / nums.length;
}

function formatHandicapSideLine(rawLine, side, strong) {
  const line = normalizeLine(rawLine);
  if (!line) return null;
  if (line === '0' || line === '0.0') return '0';
  if (/^[+-]/.test(line)) return line;

  if (strong === 'H') return (side === 'H' ? '-' : '+') + line;
  if (strong === 'C') return (side === 'H' ? '+' : '-') + line;
  return line;
}

function uniqBy(items, keyFn) {
  const seen = new Set();
  const out = [];
  for (const it of items) {
    const key = keyFn(it);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(it);
  }
  return out;
}

function buildSimpleMarkets(boards, meta) {
  const ftBoards = (boards || []).filter(
    (b) => b && b.attrs && b.attrs.mode === 'FT' && (!b.attrs.ptype || b.attrs.ptype === '')
  );

  const handicapLines = [];
  const ouLines = [];

  for (const b of ftBoards) {
    const t = b.raw || {};
    const gid = b.meta && b.meta.gid ? String(b.meta.gid) : null;

    // 让球（滚球：RE）
    if (t.sw_RE === 'Y' && t.ratio_re && isPositiveOdd(t.ior_REH) && isPositiveOdd(t.ior_REC)) {
      const rawLine = normalizeLine(t.ratio_re);
      const strong = t.strong || (b.meta && b.meta.strong) || null;
      const homeLine = formatHandicapSideLine(rawLine, 'H', strong);
      const awayLine = formatHandicapSideLine(rawLine, 'C', strong);
      handicapLines.push({
        gid,
        sort: lineAverage(rawLine),
        rawLine,
        wtype: 'RE',
        strong,
        home: { team: meta.teamH, line: homeLine, odd: String(t.ior_REH) },
        away: { team: meta.teamC, line: awayLine, odd: String(t.ior_REC) }
      });
    }

    // 让球（今日/早盘：R）
    const prematchHandicapLine = t.ratio || t.ratio_r;
    if (t.sw_R === 'Y' && prematchHandicapLine && isPositiveOdd(t.ior_RH) && isPositiveOdd(t.ior_RC)) {
      const rawLine = normalizeLine(prematchHandicapLine);
      const strong = t.strong || (b.meta && b.meta.strong) || null;
      const homeLine = formatHandicapSideLine(rawLine, 'H', strong);
      const awayLine = formatHandicapSideLine(rawLine, 'C', strong);
      handicapLines.push({
        gid,
        sort: lineAverage(rawLine),
        rawLine,
        wtype: 'R',
        strong,
        home: { team: meta.teamH, line: homeLine, odd: String(t.ior_RH) },
        away: { team: meta.teamC, line: awayLine, odd: String(t.ior_RC) }
      });
    }

    // 大小（滚球：ROU）
    const ouLine = t.ratio_rouo || t.ratio_rouu;
    if (t.sw_ROU === 'Y' && ouLine && isPositiveOdd(t.ior_ROUH) && isPositiveOdd(t.ior_ROUC)) {
      const rawLine = normalizeLine(ouLine);
      ouLines.push({
        gid,
        sort: lineAverage(rawLine),
        rawLine,
        wtype: 'ROU',
        over: { label: '大', line: rawLine, odd: String(t.ior_ROUC) },
        under: { label: '小', line: rawLine, odd: String(t.ior_ROUH) }
      });
    }

    // 大小（今日/早盘：OU）
    const prematchOuLine = t.ratio_o || t.ratio_u || t.ratio_ouo || t.ratio_ouu;
    if (t.sw_OU === 'Y' && prematchOuLine && isPositiveOdd(t.ior_OUH) && isPositiveOdd(t.ior_OUC)) {
      const rawLine = normalizeLine(prematchOuLine);
      ouLines.push({
        gid,
        sort: lineAverage(rawLine),
        rawLine,
        wtype: 'OU',
        over: { label: '大', line: rawLine, odd: String(t.ior_OUC) },
        under: { label: '小', line: rawLine, odd: String(t.ior_OUH) }
      });
    }
  }

  const mainForRm =
    ftBoards.find((b) => b && b.attrs && b.attrs.master === 'Y' && b.raw && isPositiveOdd(b.raw.ior_RMH)) ||
    ftBoards.find((b) => b && b.raw && isPositiveOdd(b.raw.ior_RMH)) ||
    null;

  const mainForM =
    ftBoards.find((b) => b && b.attrs && b.attrs.master === 'Y' && b.raw && isPositiveOdd(b.raw.ior_MH)) ||
    ftBoards.find((b) => b && b.raw && isPositiveOdd(b.raw.ior_MH)) ||
    null;

  const moneyLine =
    mainForRm && mainForRm.raw && isPositiveOdd(mainForRm.raw.ior_RMH) && isPositiveOdd(mainForRm.raw.ior_RMN) && isPositiveOdd(mainForRm.raw.ior_RMC)
      ? {
          gid: mainForRm.meta && mainForRm.meta.gid ? String(mainForRm.meta.gid) : null,
          home: { team: meta.teamH, odd: String(mainForRm.raw.ior_RMH) },
          draw: { label: '和局', odd: String(mainForRm.raw.ior_RMN) },
          away: { team: meta.teamC, odd: String(mainForRm.raw.ior_RMC) }
        }
      : mainForM && mainForM.raw && isPositiveOdd(mainForM.raw.ior_MH) && isPositiveOdd(mainForM.raw.ior_MN) && isPositiveOdd(mainForM.raw.ior_MC)
        ? {
            gid: mainForM.meta && mainForM.meta.gid ? String(mainForM.meta.gid) : null,
            home: { team: meta.teamH, odd: String(mainForM.raw.ior_MH) },
            draw: { label: '和局', odd: String(mainForM.raw.ior_MN) },
            away: { team: meta.teamC, odd: String(mainForM.raw.ior_MC) }
          }
        : null;

  // 按 get_game_more XML 的出现顺序展示（更贴近原站），只做“首次出现优先”的去重。
  // 注意：同一个 ratio_re 可能会出现 strong 不同的两条盘（例如：-0/0.5 与 +0/0.5），不能只按 rawLine 去重。
  const handicap = uniqBy(handicapLines, (x) => `${x.wtype || ''}|${x.home && x.home.line ? x.home.line : ''}|${x.away && x.away.line ? x.away.line : ''}`);
  const overUnder = uniqBy(ouLines, (x) => `${x.wtype || ''}|${x.rawLine}`);

  return {
    handicap: { title: '让球', lines: handicap },
    overUnder: { title: '大小', lines: overUnder },
    moneyLine: moneyLine ? { title: '独赢', ...moneyLine } : { title: '独赢', empty: true }
  };
}

class HgaSession {
  constructor({ targetUrl }) {
    this.targetUrl = targetUrl || 'https://hga027.com/';

    this.browser = null;
    this.context = null;
    this.page = null;

    this.uid = null;
    this.ver = null;
    this.langx = 'zh-cn';
    this.ltype = '4';
    this.oddFType = 'H';

    this.ftOrderViewCache = new Map();

    this.startedAt = null;
    this.loggedInAt = null;
    this.lastError = null;
  }

  static normalizeMode(mode) {
    const m = String(mode || 'live')
      .trim()
      .toLowerCase();
    if (m === 'live' || m === 'rb' || m === 'running') return 'live';
    if (m === 'today') return 'today';
    if (m === 'early') return 'early';
    return 'live';
  }

  get started() {
    return Boolean(this.browser);
  }

  get loggedIn() {
    return Boolean(this.uid && this.ver);
  }

  getStatus() {
    return {
      started: this.started,
      loggedIn: this.loggedIn,
      uidMasked: maskUid(this.uid),
      ver: this.ver || null,
      langx: this.langx || null,
      ltype: this.ltype || null,
      startedAt: this.startedAt,
      loggedInAt: this.loggedInAt,
      lastError: this.lastError
    };
  }

  async start() {
    if (this.started) return;

    this.startedAt = safeNowIso();
    this.lastError = null;

    try {
      this.browser = await chromium.launch({ headless: false });
      this.context = await this.browser.newContext();
      this.page = await this.context.newPage();

      this.page.on('request', (request) => this.#maybeCaptureTransformMetaFromRequest(request));
      this.page.on('response', (response) => this.#maybeCaptureLoginFromResponse(response));

      await this.page.goto(this.targetUrl, { waitUntil: 'domcontentloaded' });
    } catch (error) {
      this.lastError = error && error.message ? error.message : String(error);
      await this.stop();
      throw error;
    }
  }

  async stop() {
    try {
      if (this.context) await this.context.close();
    } catch {}
    try {
      if (this.browser) await this.browser.close();
    } catch {}

    this.browser = null;
    this.context = null;
    this.page = null;

    this.uid = null;
    this.ver = null;
    this.loggedInAt = null;

    try {
      this.ftOrderViewCache.clear();
    } catch {}
  }

  #maybeCaptureTransformMetaFromRequest(request) {
    try {
      const url = request.url();
      const u = new URL(url);
      if (u.origin !== safeOrigin(this.targetUrl)) return;
      if (u.pathname !== '/transform.php') return;
      if (request.method() !== 'POST') return;

      const body = request.postData();
      const form = parseForm(body);
      if (!form) return;

      if (form.langx && !this.langx) this.langx = form.langx;
      if (form.ltype && !this.ltype) this.ltype = String(form.ltype);
      if (form.ver && !this.ver) this.ver = String(form.ver);
      if (form.odd_f_type) this.oddFType = String(form.odd_f_type);

      // 登录后页面会不断带 uid 请求各种接口，这里可以作为兜底抓取 uid。
      if (form.uid && !this.uid) {
        this.uid = String(form.uid);
        this.loggedInAt = this.loggedInAt || safeNowIso();
      }
    } catch {}
  }

  async #maybeCaptureLoginFromResponse(response) {
    try {
      const request = response.request();
      const url = request.url();
      const u = new URL(url);
      if (u.origin !== safeOrigin(this.targetUrl)) return;
      if (u.pathname !== '/transform.php') return;
      if (request.method() !== 'POST') return;

      const body = request.postData();
      if (!body) return;

      const form = parseForm(body);
      if (!form) return;
      if (form.p !== 'chk_login') return;

      const text = await response.text();
      const status = extractTag(text, 'status');
      if (status !== '200') return;

      const uid = extractTag(text, 'uid');
      const ltype = extractTag(text, 'ltype');
      const oddF = extractTag(text, 'odd_f');

      if (form.ver && !this.ver) this.ver = String(form.ver);
      if (form.langx && !this.langx) this.langx = String(form.langx);
      if (uid) this.uid = uid;
      if (ltype) this.ltype = String(ltype);
      if (oddF && typeof oddF === 'string') this.oddFType = oddF.split(',')[0] || this.oddFType;

      this.loggedInAt = this.loggedInAt || safeNowIso();
    } catch {}
  }

  #assertReady() {
    if (!this.started || !this.context) {
      throw new Error('浏览器还没启动：请先在页面点“启动浏览器”');
    }
    if (!this.loggedIn) {
      throw new Error('还没检测到登录：请在弹出的浏览器里先手动登录');
    }
  }

  async #postTransform(form, { timeoutMs } = {}) {
    this.#assertReady();

    // Playwright 文档：browserContext.request / page.request 会复用 context cookies。
    const api = this.context.request;
    const url = `${safeOrigin(this.targetUrl)}/transform.php?ver=${encodeURIComponent(this.ver)}`;

    const response = await api.post(url, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      form,
      ...(timeoutMs != null ? { timeout: timeoutMs } : {})
    });

    const status = response.status();
    const text = await response.text();
    if (status < 200 || status >= 300) {
      throw new Error(`transform.php HTTP ${status}`);
    }
    return text;
  }

  async getGameList() {
    return this.getGameListByMode({ mode: 'live' });
  }

  async getGameListByMode({ mode = 'live', lid } = {}) {
    const now = Date.now().toString();
    const m = HgaSession.normalizeMode(mode);

    const showtype = m;
    const rtype = m === 'live' ? 'rb' : 'r';
    const filter = m === 'today' ? 'FT' : m === 'early' ? 'FU' : '';
    const date = m === 'early' ? 'all' : '';

    /** @type {Record<string, string>} */
    const form = {
      uid: this.uid,
      ver: this.ver,
      langx: this.langx,
      p: 'get_game_list',
      p3type: '',
      date,
      gtype: 'ft',
      showtype,
      rtype,
      ltype: this.ltype,
      filter,
      cupFantasy: 'N',
      sorttype: 'L',
      specialClick: '',
      isFantasy: 'N',
      ts: now,
      chgSortTS: now
    };
    if (m === 'early' && lid) {
      form.lid = String(lid);
      form.action = 'click_league';
    }

    const xmlText = await this.#postTransform(form);

    return parseGameListXml(xmlText);
  }

  async getGameMore({ lid, ecid, filter = 'Main', mode = 'live' }) {
    const now = Date.now().toString();
    const m = HgaSession.normalizeMode(mode);
    const showtype = m;
    const isRB = m === 'live' ? 'Y' : 'N';
    const xmlText = await this.#postTransform({
      uid: this.uid,
      ver: this.ver,
      langx: this.langx,
      p: 'get_game_more',
      gtype: 'ft',
      showtype,
      ltype: this.ltype,
      isRB,
      lid: String(lid),
      specialClick: '',
      mode: 'NORMAL',
      from: 'game_more',
      filter,
      ts: now,
      ecid: String(ecid)
    });

    return parseGameMoreXml(xmlText);
  }

  async #ftOrderView({ gid, wtype, choseTeam }) {
    const key = `${gid}|${wtype}|${choseTeam}|${this.oddFType || ''}`;
    const now = Date.now();
    const cached = this.ftOrderViewCache.get(key);
    if (cached && cached.expiresAt > now) return cached.value;
    if (cached) this.ftOrderViewCache.delete(key);

    const xmlText = await this.#postTransform({
      p: 'FT_order_view',
      uid: this.uid,
      ver: this.ver,
      langx: this.langx,
      odd_f_type: this.oddFType,
      gid: String(gid),
      gtype: 'FT',
      wtype: String(wtype),
      chose_team: String(choseTeam)
    }, { timeoutMs: 2500 });
    const parsed = parseFtOrderViewXml(xmlText);
    if (parsed && parsed.ioratio) {
      this.ftOrderViewCache.set(key, { value: parsed, expiresAt: now + FT_ORDER_VIEW_CACHE_MS });
    }
    // 简单防护：极端情况下（反复切换多场/多盘口）避免缓存无限增长
    if (this.ftOrderViewCache.size > 800) {
      const t = Date.now();
      for (const [k, v] of this.ftOrderViewCache) {
        if (!v || v.expiresAt <= t) this.ftOrderViewCache.delete(k);
      }
      if (this.ftOrderViewCache.size > 800) this.ftOrderViewCache.clear();
    }
    return parsed;
  }

  async #confirmSimpleMarkets(simple) {
    if (!simple) return;

    const reqs = new Map();
    const add = (gid, wtype, choseTeam) => {
      if (!gid) return;
      const key = `${gid}|${wtype}|${choseTeam}`;
      if (reqs.has(key)) return;
      reqs.set(key, { gid, wtype, choseTeam });
    };

    const handicapLines = (simple.handicap && Array.isArray(simple.handicap.lines) ? simple.handicap.lines : []) || [];
    for (const row of handicapLines) {
      const wtype = row.wtype || 'RE';
      add(row.gid, wtype, 'H');
      add(row.gid, wtype, 'C');
    }

    const ouLines = (simple.overUnder && Array.isArray(simple.overUnder.lines) ? simple.overUnder.lines : []) || [];
    for (const row of ouLines) {
      const wtype = row.wtype || 'ROU';
      add(row.gid, wtype, 'C');
      add(row.gid, wtype, 'H');
    }

    const MAX_REQ = 40;

    /** @type {Map<string, any>} */
    const out = new Map();
    const sliced = [...reqs.entries()].slice(0, MAX_REQ);
    // 并发请求可以大幅降低“校验水位”耗时，但需要控制并发避免目标站点限流。
    const CONCURRENCY = 8;
    for (let i = 0; i < sliced.length; i += CONCURRENCY) {
      const batch = sliced.slice(i, i + CONCURRENCY);
      await Promise.all(
        batch.map(async ([key, req]) => {
          try {
            out.set(key, await this.#ftOrderView(req));
          } catch {}
        })
      );
    }

    for (const row of handicapLines) {
      const wtype = row.wtype || 'RE';
      const h = out.get(`${row.gid}|${wtype}|H`);
      const c = out.get(`${row.gid}|${wtype}|C`);
      if (h && h.ioratio) row.home.odd = String(h.ioratio);
      if (c && c.ioratio) row.away.odd = String(c.ioratio);
    }

    for (const row of ouLines) {
      const wtype = row.wtype || 'ROU';
      const over = out.get(`${row.gid}|${wtype}|C`);
      const under = out.get(`${row.gid}|${wtype}|H`);
      if (over && over.ioratio) row.over.odd = String(over.ioratio);
      if (under && under.ioratio) row.under.odd = String(under.ioratio);
    }
  }

  async resolveFtGameByGid(requestedGid, { mode = 'live', lid } = {}) {
    const m = HgaSession.normalizeMode(mode);
    const { games } = await this.getGameListByMode({ mode: m, lid });
    const gid = String(requestedGid);

    const found = games.find((g) => String(g.GID) === gid);
    if (!found) {
      const label = m === 'live' ? '滚球' : m === 'today' ? '今日' : '早盘';
      throw new Error(`找不到这个 gid：请先点“获取比赛列表-${label}”刷新列表（或确认 Chromium 里已登录，并在足球-${label}页面）`);
    }

    return {
      requestedGid: gid,
      gid: String(found.GID),
      ecid: String(found.ECID),
      lid: String(found.LID),
      mtId: found.MT_ID ? String(found.MT_ID) : null,
      league: found.LEAGUE ? String(found.LEAGUE) : null,
      teamH: found.TEAM_H ? String(found.TEAM_H) : null,
      teamC: found.TEAM_C ? String(found.TEAM_C) : null,
      mode: m
    };
  }

  async resolveLiveFtGameByGid(requestedGid) {
    return this.resolveFtGameByGid(requestedGid, { mode: 'live' });
  }

  async getOddsSnapshotByResolved(resolved, { includeRaw = false, confirmOdds = false } = {}) {
    const gameMore = await this.getGameMore({ lid: resolved.lid, ecid: resolved.ecid, mode: resolved.mode || 'live' });
    const boards = Array.isArray(gameMore.boards) ? gameMore.boards : [];
    const mainBoard =
      boards.find((b) => b && b.attrs && b.attrs.master === 'Y') ||
      boards.find((b) => b && b.meta && String(b.meta.gid) === String(resolved.gid)) ||
      boards[0] ||
      null;

    const score =
      mainBoard && mainBoard.meta && mainBoard.meta.score_h != null && mainBoard.meta.score_c != null
        ? `${mainBoard.meta.score_h}:${mainBoard.meta.score_c}`
        : null;

    const meta = {
      requestedGid: resolved.requestedGid,
      masterGid: (mainBoard && mainBoard.meta && mainBoard.meta.gid) || resolved.gid,
      league: (mainBoard && mainBoard.meta && mainBoard.meta.league) || resolved.league,
      teamH: (mainBoard && mainBoard.meta && mainBoard.meta.team_h) || resolved.teamH,
      teamC: (mainBoard && mainBoard.meta && mainBoard.meta.team_c) || resolved.teamC,
      score,
      mode: resolved.mode || 'live',
      reTime: (mainBoard && mainBoard.meta && mainBoard.meta.re_time) || null,
      datetime: (mainBoard && mainBoard.meta && mainBoard.meta.datetime) || null,
      systime: (mainBoard && mainBoard.meta && mainBoard.meta.systime) || gameMore.systime || null,
      lid: (mainBoard && mainBoard.meta && mainBoard.meta.lid) || resolved.lid,
      ecid: gameMore.ecid || resolved.ecid,
      mtId: resolved.mtId
    };

    const simple = buildSimpleMarkets(boards, meta);
    if (confirmOdds) {
      await this.#confirmSimpleMarkets(simple);
    }

    return {
      meta,
      ftGids: gameMore.ftGids,
      simple,
      ...(includeRaw
        ? {
            boards: boards.map((b) => ({
              attrs: b.attrs,
              meta: b.meta,
              markets: b.markets
            })),
            ...(mainBoard ? { raw: mainBoard.raw } : {})
          }
        : {})
    };
  }
}

module.exports = { HgaSession };
