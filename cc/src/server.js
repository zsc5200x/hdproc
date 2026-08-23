const path = require('node:path');

const express = require('express');

const { HgaSession } = require('./hga-session');

const PORT = Number(process.env.PORT || 3000);
const TARGET_URL = process.env.TARGET_URL || 'https://hga027.com/';
const DEFAULT_REFRESH_MS = Number(process.env.REFRESH_MS || 3000);

function asyncHandler(fn) {
  return (req, res, next) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function asString(v) {
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

const session = new HgaSession({ targetUrl: TARGET_URL });

const app = express();
app.disable('x-powered-by');

app.use(express.json());
app.use(express.urlencoded({ extended: false }));

app.use(express.static(path.join(__dirname, '..', 'public')));

app.get('/api/status', (req, res) => {
  res.json({ ok: true, targetUrl: TARGET_URL, ...session.getStatus() });
});

app.post(
  '/api/start',
  asyncHandler(async (req, res) => {
    await session.start();
    res.json({ ok: true, ...session.getStatus() });
  })
);

app.post(
  '/api/stop',
  asyncHandler(async (req, res) => {
    await session.stop();
    res.json({ ok: true, ...session.getStatus() });
  })
);

app.get(
  '/api/games',
  asyncHandler(async (req, res) => {
    const mode = HgaSession.normalizeMode(asString(req.query.mode));
    const lid = asString(req.query.lid).trim();

    const { games } = await session.getGameListByMode({
      mode,
      ...(lid ? { lid } : {})
    });
    const simplified = games.map((g) => ({
      ...(g.SCORE_H != null && String(g.SCORE_H).trim() !== '' && g.SCORE_C != null && String(g.SCORE_C).trim() !== ''
        ? { score: `${String(g.SCORE_H).trim()}:${String(g.SCORE_C).trim()}` }
        : { score: null }),
      gid: g.GID,
      league: g.LEAGUE,
      teamH: g.TEAM_H,
      teamC: g.TEAM_C,
      time: g.RETIMESET || g.DATETIME || g.GAME_DATE_TIME || null,
      moreCount: g.MORE != null ? Number(g.MORE) : null
    }));
    res.json({ ok: true, mode, games: simplified });
  })
);

app.get(
  '/api/odds',
  asyncHandler(async (req, res) => {
    const gid = asString(req.query.gid).trim();
    if (!gid) {
      res.status(400).json({ ok: false, error: '缺少 gid：/api/odds?gid=8513893' });
      return;
    }

    const mode = HgaSession.normalizeMode(asString(req.query.mode));
    const includeRaw = /^(1|true|yes)$/i.test(asString(req.query.raw));
    const confirmOdds = /^(1|true|yes)$/i.test(asString(req.query.confirm));
    const resolved = await session.resolveFtGameByGid(gid, { mode });
    const snapshot = await session.getOddsSnapshotByResolved(resolved, { includeRaw, confirmOdds });
    res.json({ ok: true, ...snapshot });
  })
);

app.get(
  '/api/stream',
  asyncHandler(async (req, res) => {
    const gid = asString(req.query.gid).trim();
    if (!gid) {
      res.status(400).json({ ok: false, error: '缺少 gid：/api/stream?gid=8513893' });
      return;
    }

    const refreshMs = clamp(Number(req.query.refreshMs || DEFAULT_REFRESH_MS), 1000, 30000);
    const mode = HgaSession.normalizeMode(asString(req.query.mode));
    const includeRaw = /^(1|true|yes)$/i.test(asString(req.query.raw));
    const confirmOdds = /^(1|true|yes)$/i.test(asString(req.query.confirm));

    res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    if (typeof res.flushHeaders === 'function') res.flushHeaders();

    const send = (event, data) => {
      if (event) res.write(`event: ${event}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    let timer = null;
    let closed = false;
    let inFlight = false;

    req.on('close', () => {
      closed = true;
      if (timer) clearInterval(timer);
      timer = null;
    });

    let resolved;
    try {
      resolved = await session.resolveFtGameByGid(gid, { mode });
    } catch (error) {
      send('error', { message: error && error.message ? error.message : String(error) });
      res.end();
      return;
    }

    send('status', { ok: true, refreshMs, gid: resolved.gid, mode: resolved.mode, confirmOdds });

    const tick = async () => {
      if (closed) return;
      if (inFlight) return;
      inFlight = true;
      try {
        const snapshot = await session.getOddsSnapshotByResolved(resolved, { includeRaw, confirmOdds });
        send('data', snapshot);
      } catch (error) {
        send('error', { message: error && error.message ? error.message : String(error) });
      } finally {
        inFlight = false;
      }
    };

    await tick();
    timer = setInterval(tick, refreshMs);
  })
);

app.use((req, res) => {
  res.status(404).json({ ok: false, error: `Not Found: ${req.method} ${req.url}` });
});

app.use((err, req, res, next) => {
  const message = err && err.message ? err.message : String(err);
  console.error('[server error]', message);
  res.status(500).json({ ok: false, error: message });
});

app.listen(PORT, () => {
  console.log(`WUI 已启动：http://localhost:${PORT}`);
  console.log(`目标站点：${TARGET_URL}`);
});
