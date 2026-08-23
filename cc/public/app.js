let statusTimer = null;
let stream = null;
let currentMode = 'live';
let allGames = [];
let prevOdds = new Map();
let lastStatusToastAt = 0;

const storage = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v == null ? fallback : v;
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, String(value));
    } catch {}
  }
};

function $(id) {
  return document.getElementById(id);
}

function setText(id, text) {
  const el = $(id);
  if (!el) return;
  el.textContent = text == null || text === '' ? '-' : String(text);
}

function h(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function toast(message, kind = 'info', timeoutMs = 2600) {
  const box = $('toasts');
  if (!box) return;

  const el = document.createElement('div');
  el.className = `toast ${kind === 'good' ? 'good' : kind === 'bad' ? 'bad' : ''}`.trim();
  el.textContent = message;
  box.appendChild(el);

  const t = window.setTimeout(() => {
    try {
      el.remove();
    } catch {}
  }, timeoutMs);

  el.addEventListener('click', () => {
    window.clearTimeout(t);
    try {
      el.remove();
    } catch {}
  });
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = data && data.error ? data.error : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

function modeLabel(mode) {
  if (mode === 'today') return '今日';
  if (mode === 'early') return '早盘';
  return '滚球';
}

function setMode(mode) {
  currentMode = mode;
  storage.set('hga_wui_mode', mode);
  setText('stMode', modeLabel(mode));

  const btnLive = $('btnLoadLive');
  const btnToday = $('btnLoadToday');
  const btnEarly = $('btnLoadEarly');

  if (btnLive) btnLive.classList.toggle('primary', mode === 'live');
  if (btnToday) btnToday.classList.toggle('primary', mode === 'today');
  if (btnEarly) btnEarly.classList.toggle('primary', mode === 'early');
}

function renderStatus(st) {
  setText('stTarget', st.targetUrl);
  setText('stTarget2', st.targetUrl);
  setText('stStarted', st.started ? '是' : '否');
  setText('stLoggedIn', st.loggedIn ? '是' : '否');
  setText('stUid', st.uidMasked || '-');
  setText('stVer', st.ver || '-');
  setText('stErr', st.lastError || '-');

  setText('chipStarted', `浏览器：${st.started ? '已启动' : '未启动'}`);
  setText('chipLoggedIn', `登录：${st.loggedIn ? '已登录' : '未登录'}`);
  setText('chipUid', `uid：${st.uidMasked || '-'}`);
}

async function refreshStatus() {
  try {
    const st = await fetchJson('/api/status');
    renderStatus(st);
  } catch (e) {
    const msg = e.message || String(e);
    setText('stErr', msg);
    const now = Date.now();
    if (now - lastStatusToastAt > 6000) {
      lastStatusToastAt = now;
      toast(`状态获取失败：${msg}`, 'bad');
    }
  }
}

function clearGamesTable() {
  allGames = [];
  const box = $('gamesList');
  if (box) box.innerHTML = '';
}

function normalizeText(s) {
  return String(s || '')
    .trim()
    .toLowerCase();
}

function setView(viewId) {
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('isHidden', v.id !== viewId));
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('isActive', t.getAttribute('data-view') === viewId));
}

function renderGames(games) {
  allGames = Array.isArray(games) ? games : [];
  applyGamesFilter();
}

function applyGamesFilter() {
  const q = normalizeText(($('inSearch') && $('inSearch').value) || '');
  const box = $('gamesList');
  if (!box) return;

  box.innerHTML = '';

  const filtered = q
    ? allGames.filter((g) => {
        const hay = `${g.gid} ${g.league || ''} ${g.teamH || ''} ${g.teamC || ''} ${g.time || ''}`.toLowerCase();
        return hay.includes(q);
      })
    : allGames;

  if (filtered.length === 0) {
    box.innerHTML = '<div class="hint">没有匹配的比赛</div>';
    return;
  }

  for (const g of filtered) {
    const card = document.createElement('div');
    card.className = 'gameCard';

    const gid = h(g.gid);
    const league = h(g.league || '');
    const time = h(g.time || '');
    const score = h(g.score || '');
    const teamH = h(g.teamH || '');
    const teamC = h(g.teamC || '');
    const more = g.moreCount == null ? null : g.moreCount;

    card.innerHTML = `
      <div class="gameTop">
        <div class="gameLeague" title="${league}">${league}</div>
        <div class="gameTime mono">${time}</div>
      </div>
      <div class="gameTeams">
        <b>${teamH}</b> vs <b>${teamC}</b>
      </div>
      <div class="gameBottom">
        <div class="badges">
          <span class="badge mono">gid: ${gid}</span>
          ${score ? `<span class="badge mono">比分: ${score}</span>` : ''}
          ${more != null ? `<span class="badge mono">更多盘: ${more}</span>` : ''}
        </div>
        <div class="actions">
          <button data-act="copy" data-gid="${gid}" type="button">复制</button>
          <button data-act="watch" data-gid="${gid}" class="btnPrimary" type="button">监控</button>
        </div>
      </div>
    `;

    box.appendChild(card);
  }

  box.querySelectorAll('button[data-act="copy"]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const gid = btn.getAttribute('data-gid') || '';
      try {
        await navigator.clipboard.writeText(gid);
        toast(`已复制 gid：${gid}`, 'good');
      } catch {
        toast('复制失败（浏览器不支持剪贴板）', 'bad');
      }
    });
  });

  box.querySelectorAll('button[data-act="watch"]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const gid = btn.getAttribute('data-gid');
      if (!gid) return;
      $('inGid').value = gid;
      setView('viewMonitor');
      startStream().catch(() => {});
    });
  });
}

function renderMeta(meta, ftGids) {
  const rows = [];

  const push = (k, v) =>
    rows.push(`<div><span>${h(k)}</span><span class="mono">${v == null ? '-' : h(v)}</span></div>`);

  push('模式', modeLabel(meta.mode));
  push('你输入的GID', meta.requestedGid);
  push('主盘GID', meta.masterGid);
  push('联赛', meta.league);
  push('对阵', `${meta.teamH || ''} vs ${meta.teamC || ''}`);
  push('比分', meta.score);
  push('时间', meta.reTime || meta.datetime);
  push('服务器时间', meta.systime);
  push('联赛ID(LID)', meta.lid);
  push('事件ID(ECID)', meta.ecid);
  push('数据ID(MT_ID)', meta.mtId);
  if (Array.isArray(ftGids) && ftGids.length) {
    push(
      '同场比赛GID列表',
      ftGids
        .map((x) => `${x.gid}${x.master === 'Y' ? '(主盘)' : ''}`)
        .join(', ')
    );
  }

  $('boxMeta').innerHTML = rows.join('');
}

function clearSimple() {
  $('boxHandicap').innerHTML = '';
  $('boxOverUnder').innerHTML = '';
  $('boxMoneyLine').innerHTML = '';
}

function oddNumber(v) {
  const n = Number.parseFloat(String(v || ''));
  return Number.isFinite(n) ? n : Number.NaN;
}

function diffKey(parts) {
  return parts.filter((x) => x != null && String(x).trim() !== '').join('|');
}

function pickChange(key, oddValue) {
  const n = oddNumber(oddValue);
  if (!Number.isFinite(n)) return null;

  const prev = prevOdds.get(key);
  prevOdds.set(key, n);
  if (!Number.isFinite(prev) || prev === n) return null;
  return n > prev ? 'up' : 'down';
}

function renderHandicap(lines) {
  const box = $('boxHandicap');
  box.innerHTML = '';

  if (!Array.isArray(lines) || lines.length === 0) {
    box.innerHTML = '<div class="hint">暂无让球盘口</div>';
    return;
  }

  for (const row of lines) {
    const wrap = document.createElement('div');
    wrap.className = 'pairRow';

    const left = document.createElement('div');
    left.className = 'pick';
    {
      const key = diffKey(['handicap', row.gid, row.wtype, row.home && row.home.line, 'H']);
      const change = pickChange(key, row.home && row.home.odd);
      if (change) left.setAttribute('data-change', change);
    }
    left.innerHTML = `
      <div class="pickTop">${h(row.home.team)} <span class="mono">${h(row.home.line)}</span></div>
      <div class="odd mono">${h(row.home.odd)}</div>
    `;

    const right = document.createElement('div');
    right.className = 'pick';
    {
      const key = diffKey(['handicap', row.gid, row.wtype, row.away && row.away.line, 'C']);
      const change = pickChange(key, row.away && row.away.odd);
      if (change) right.setAttribute('data-change', change);
    }
    right.innerHTML = `
      <div class="pickTop">${h(row.away.team)} <span class="mono">${h(row.away.line)}</span></div>
      <div class="odd mono">${h(row.away.odd)}</div>
    `;

    wrap.appendChild(left);
    wrap.appendChild(right);
    box.appendChild(wrap);
  }
}

function renderOverUnder(lines) {
  const box = $('boxOverUnder');
  box.innerHTML = '';

  if (!Array.isArray(lines) || lines.length === 0) {
    box.innerHTML = '<div class="hint">暂无大小盘口</div>';
    return;
  }

  for (const row of lines) {
    const wrap = document.createElement('div');
    wrap.className = 'pairRow';

    const left = document.createElement('div');
    left.className = 'pick';
    {
      const key = diffKey(['ou', row.gid, row.wtype, row.over && row.over.line, 'O']);
      const change = pickChange(key, row.over && row.over.odd);
      if (change) left.setAttribute('data-change', change);
    }
    left.innerHTML = `
      <div class="pickTop">${h(row.over.label)} <span class="mono">${h(row.over.line)}</span></div>
      <div class="odd mono">${h(row.over.odd)}</div>
    `;

    const right = document.createElement('div');
    right.className = 'pick';
    {
      const key = diffKey(['ou', row.gid, row.wtype, row.under && row.under.line, 'U']);
      const change = pickChange(key, row.under && row.under.odd);
      if (change) right.setAttribute('data-change', change);
    }
    right.innerHTML = `
      <div class="pickTop">${h(row.under.label)} <span class="mono">${h(row.under.line)}</span></div>
      <div class="odd mono">${h(row.under.odd)}</div>
    `;

    wrap.appendChild(left);
    wrap.appendChild(right);
    box.appendChild(wrap);
  }
}

function renderMoneyLine(moneyLine) {
  const box = $('boxMoneyLine');
  box.innerHTML = '';

  if (!moneyLine || moneyLine.empty) {
    box.innerHTML = '<div class="hint">暂无独赢盘口</div>';
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'tripleRow';
  wrap.innerHTML = `
    <div class="pick" data-k="ml-h">
      <div class="pickTop">${h(moneyLine.home.team)}</div>
      <div class="odd mono">${h(moneyLine.home.odd)}</div>
    </div>
    <div class="pick" data-k="ml-n">
      <div class="pickTop">${h(moneyLine.draw.label)}</div>
      <div class="odd mono">${h(moneyLine.draw.odd)}</div>
    </div>
    <div class="pick" data-k="ml-c">
      <div class="pickTop">${h(moneyLine.away.team)}</div>
      <div class="odd mono">${h(moneyLine.away.odd)}</div>
    </div>
  `;

  try {
    const h = wrap.querySelector('[data-k="ml-h"]');
    const n = wrap.querySelector('[data-k="ml-n"]');
    const c = wrap.querySelector('[data-k="ml-c"]');

    if (h) {
      const key = diffKey(['ml', moneyLine.gid, 'H']);
      const change = pickChange(key, moneyLine.home && moneyLine.home.odd);
      if (change) h.setAttribute('data-change', change);
    }
    if (n) {
      const key = diffKey(['ml', moneyLine.gid, 'N']);
      const change = pickChange(key, moneyLine.draw && moneyLine.draw.odd);
      if (change) n.setAttribute('data-change', change);
    }
    if (c) {
      const key = diffKey(['ml', moneyLine.gid, 'C']);
      const change = pickChange(key, moneyLine.away && moneyLine.away.odd);
      if (change) c.setAttribute('data-change', change);
    }
  } catch {}

  box.appendChild(wrap);
}

function renderSimple(simple) {
  if (!simple) {
    clearSimple();
    return;
  }
  renderHandicap(simple.handicap && simple.handicap.lines);
  renderOverUnder(simple.overUnder && simple.overUnder.lines);
  renderMoneyLine(simple.moneyLine);
}

function stopStream() {
  if (stream) stream.close();
  stream = null;
  setText('stStream', '已停止');
  setText('stLastUpdate', '');
  prevOdds = new Map();
  clearSimple();
}

async function startStream() {
  stopStream();

  const gid = $('inGid').value.trim();
  if (!gid) {
    setText('stStream', '请先输入 gid');
    return;
  }
  storage.set('hga_wui_gid', gid);

  const refreshMs = Number($('inRefresh').value || 3000);
  const raw = $('ckRaw').checked ? '1' : '0';
  const confirm = $('ckConfirm').checked ? '1' : '0';

  const url = `/api/stream?gid=${encodeURIComponent(gid)}&mode=${encodeURIComponent(currentMode)}&refreshMs=${encodeURIComponent(String(refreshMs))}&confirm=${confirm}&raw=${raw}`;
  stream = new EventSource(url);

  setText('stStream', '连接中…');
  setText('stLastUpdate', '');

  stream.addEventListener('status', (e) => {
    try {
      const st = JSON.parse(e.data);
      const suffix = st.confirmOdds ? ' (已校验水位)' : '';
      const label = st.mode ? modeLabel(st.mode) : modeLabel(currentMode);
      setText('stStream', `已连接：${label} refresh=${st.refreshMs}ms${suffix}`);
      toast(`监控已连接：${gid}（${label}）`, 'good');
    } catch {
      setText('stStream', '已连接');
    }
  });

  stream.addEventListener('data', (e) => {
    const payload = JSON.parse(e.data);
    renderMeta(payload.meta, payload.ftGids);
    renderSimple(payload.simple);
    if (payload && payload.meta && payload.meta.systime) {
      setText('stLastUpdate', `更新：${payload.meta.systime}`);
    } else {
      setText('stLastUpdate', `更新：${new Date().toLocaleTimeString()}`);
    }

    const wrap = $('boxRawWrap');
    const pre = $('boxRaw');
    if (payload.raw) {
      wrap.style.display = '';
      pre.textContent = JSON.stringify(payload.raw, null, 2);
    } else {
      wrap.style.display = 'none';
      pre.textContent = '';
    }
  });

  stream.addEventListener('error', (e) => {
    // 1) 服务器发送的 event:error，可能带 data
    // 2) 浏览器的网络错误，也会触发 error（不带 data）
    if (e && e.data) {
      try {
        const err = JSON.parse(e.data);
        setText('stStream', `错误：${err.message || 'unknown'}`);
      } catch {
        setText('stStream', '错误：未知');
      }
      return;
    }
    setText('stStream', '连接断开（检查服务端是否还在运行）');
    toast('监控连接断开', 'bad');
  });
}

function bindUi() {
  // 顶部 Tab
  document.querySelectorAll('.tab[data-view]').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.getAttribute('data-view')));
  });

  // 帮助弹窗
  const dlg = $('dlgHelp');
  const helpBtn = $('btnHelp');
  if (dlg && helpBtn) {
    helpBtn.addEventListener('click', () => {
      try {
        dlg.showModal();
      } catch {
        toast('当前浏览器不支持 dialog', 'bad');
      }
    });
  }

  const search = $('inSearch');
  if (search) {
    let t = null;
    search.addEventListener('input', () => {
      if (t) window.clearTimeout(t);
      t = window.setTimeout(() => applyGamesFilter(), 160);
    });
  }

  $('btnStart').addEventListener('click', async () => {
    setText('stErr', '-');
    try {
      await fetchJson('/api/start', { method: 'POST' });
      await refreshStatus();
      toast('浏览器已启动（请在弹窗里登录）', 'good');
    } catch (e) {
      const msg = e.message || String(e);
      setText('stErr', msg);
      toast(msg, 'bad');
    }
  });

  $('btnStop').addEventListener('click', async () => {
    stopStream();
    setText('stErr', '-');
    try {
      await fetchJson('/api/stop', { method: 'POST' });
      clearGamesTable();
      await refreshStatus();
      toast('浏览器已停止', 'good');
    } catch (e) {
      const msg = e.message || String(e);
      setText('stErr', msg);
      toast(msg, 'bad');
    }
  });

  const loadGames = async (mode) => {
    setText('stErr', '-');
    setMode(mode);
    try {
      const box = $('gamesList');
      if (box) box.innerHTML = '<div class="hint">加载中…</div>';
      const data = await fetchJson(`/api/games?mode=${encodeURIComponent(mode)}`);
      renderGames(data.games || []);
      toast(`比赛列表已刷新：${modeLabel(mode)}（${(data.games || []).length} 场）`, 'good');
    } catch (e) {
      const msg = e.message || String(e);
      setText('stErr', msg);
      toast(msg, 'bad');
      clearGamesTable();
    }
  };

  $('btnLoadGames').addEventListener('click', () => loadGames(currentMode));
  $('btnLoadLive').addEventListener('click', () => loadGames('live'));
  $('btnLoadToday').addEventListener('click', () => loadGames('today'));
  $('btnLoadEarly').addEventListener('click', () => loadGames('early'));

  $('btnStartStream').addEventListener('click', () => startStream());
  $('btnStopStream').addEventListener('click', () => stopStream());

  const copyBtn = $('btnCopyGid');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const gid = ($('inGid').value || '').trim();
      if (!gid) {
        toast('没有 gid 可复制', 'bad');
        return;
      }
      try {
        await navigator.clipboard.writeText(gid);
        toast(`已复制 gid：${gid}`, 'good');
      } catch {
        toast('复制失败（浏览器不支持剪贴板）', 'bad');
      }
    });
  }

  const gidInput = $('inGid');
  if (gidInput) {
    gidInput.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      startStream().catch(() => {});
    });
  }
}

async function main() {
  bindUi();
  const lastMode = storage.get('hga_wui_mode', 'live');
  const lastGid = storage.get('hga_wui_gid', '');

  setMode(lastMode);
  await refreshStatus();
  statusTimer = setInterval(refreshStatus, 1500);

  // 默认隐藏 raw
  $('boxRawWrap').style.display = 'none';

  if (lastGid) $('inGid').value = lastGid;
  setView('viewGames');
}

main().catch((e) => {
  setText('stErr', e.message || String(e));
});
