let statusTimer = null;
let stream = null;
let currentMode = 'live';
let lastGames = [];
let lastOddByKey = new Map();

function $(id) {
  return document.getElementById(id);
}

function setText(id, text) {
  const el = $(id);
  if (!el) return;
  el.textContent = text == null || text === '' ? '-' : String(text);
}

function fmtNowTime() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function resetOddCache() {
  lastOddByKey = new Map();
}

function parseOddNumber(v) {
  const s = v == null ? '' : String(v).trim();
  if (!s) return null;
  const n = Number.parseFloat(s);
  if (!Number.isFinite(n)) return null;
  return n;
}

function decorateOdd(el, key, oddStr) {
  if (!el) return;
  const n = parseOddNumber(oddStr);
  if (n == null) return;
  const prev = lastOddByKey.has(key) ? lastOddByKey.get(key) : null;
  if (prev != null && Number.isFinite(prev)) {
    if (n > prev + 1e-6) {
      el.classList.add('oddUp', 'oddFlash');
    } else if (n < prev - 1e-6) {
      el.classList.add('oddDown', 'oddFlash');
    }
  }
  lastOddByKey.set(key, n);
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = data && (data.message || data.error) ? data.message || data.error : `HTTP ${res.status}`;
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
  setText('stMode', modeLabel(mode));

  const btnLive = $('btnLoadLive');
  const btnToday = $('btnLoadToday');
  const btnEarly = $('btnLoadEarly');

  if (btnLive) btnLive.classList.toggle('primary', mode === 'live');
  if (btnToday) btnToday.classList.toggle('primary', mode === 'today');
  if (btnEarly) btnEarly.classList.toggle('primary', mode === 'early');
}

function getSelectedMemberId() {
  const sel = $('selMember');
  return sel && sel.value ? String(sel.value) : '';
}

function renderMembers(members, selectedId) {
  const sel = $('selMember');
  if (!sel) return;

  const old = sel.value;
  const keep = selectedId || old;

  sel.innerHTML = '';
  const opt0 = document.createElement('option');
  opt0.value = '';
  opt0.textContent = '请选择会员';
  sel.appendChild(opt0);

  for (const m of members || []) {
    const opt = document.createElement('option');
    opt.value = String(m.id || '');
    const label = m.label || m.username || m.id;
    const role = m.role ? ` [${m.role}]` : '';
    const st = m.status ? ` (${m.status})` : '';
    opt.textContent = `${label}${role}${st}`;
    sel.appendChild(opt);
  }

  if (keep) sel.value = keep;
}

function renderStatus(st) {
  setText('stBaseUrl', st.base_url || '-');
  setText('stMemberStatus', st.status || '-');
  setText('stUid', st.uidMasked || '-');
  setText('stVer', st.ver || '-');
  setText('stProxy', st.proxy || '-');
  setText('stErr', st.lastError || '-');
}

async function refreshStatus() {
  try {
    const mid = getSelectedMemberId();
    const url = mid ? `/api/chashui/status?member_id=${encodeURIComponent(mid)}` : '/api/chashui/status';
    const st = await fetchJson(url);
    if (Array.isArray(st.members)) renderMembers(st.members, st.member && st.member.id ? String(st.member.id) : mid);
    if (st.member) renderStatus(st.member);
  } catch (e) {
    setText('stErr', e.message || String(e));
  }
}

function clearGamesTable() {
  const tbl = $('tblGames');
  if (!tbl) return;
  const tbody = tbl.querySelector('tbody');
  if (tbody) tbody.innerHTML = '';
  setText('stGameCount', '0');
}

function filterGames(games, q) {
  const query = String(q || '').trim().toLowerCase();
  if (!query) return games || [];
  const out = [];
  for (const g of games || []) {
    const s = [g.gid, g.league, g.teamH, g.teamC, g.score, g.time].filter(Boolean).join(' ').toLowerCase();
    if (s.includes(query)) out.push(g);
  }
  return out;
}

function renderGames(games, total) {
  const tbl = $('tblGames');
  if (!tbl) return;
  const tbody = tbl.querySelector('tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  const totalN = Number.isFinite(Number(total)) ? Number(total) : (Array.isArray(games) ? games.length : 0);
  const shownN = Array.isArray(games) ? games.length : 0;
  setText('stGameCount', totalN === shownN ? String(shownN) : `${shownN}/${totalN}`);

  for (const g of games || []) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="mono">${g.gid}</td>
      <td>${g.league || ''}</td>
      <td>${g.teamH || ''} vs ${g.teamC || ''}</td>
      <td class="mono">${g.score || ''}</td>
      <td class="mono">${g.time || ''}</td>
      <td class="mono">${g.moreCount == null ? '' : g.moreCount}</td>
      <td><button data-gid="${g.gid}">监控</button></td>
    `;
    tbody.appendChild(tr);
  }

  tbody.querySelectorAll('button[data-gid]').forEach((btn) => {
    btn.addEventListener('click', () => {
      $('inGid').value = btn.getAttribute('data-gid');
      startStream().catch(() => {});
    });
  });
}

function renderMeta(meta, ftGids) {
  const rows = [];

  const push = (k, v) => rows.push(`<div><span>${k}</span><span class="mono">${v == null ? '-' : v}</span></div>`);

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

function buildPick(label, line, odd, key) {
  const pick = document.createElement('div');
  pick.className = 'pick';

  const top = document.createElement('div');
  top.className = 'pickTop';

  const left = document.createElement('span');
  left.textContent = label || '';

  const right = document.createElement('span');
  right.className = 'mono';
  right.textContent = line || '';

  top.appendChild(left);
  top.appendChild(right);

  const oddEl = document.createElement('div');
  oddEl.className = 'odd mono';
  oddEl.textContent = odd || '';
  decorateOdd(oddEl, key, odd);

  pick.appendChild(top);
  pick.appendChild(oddEl);
  return pick;
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

    const lkey = `hc|${row.home.team || ''}|${row.home.line || ''}`;
    const rkey = `hc|${row.away.team || ''}|${row.away.line || ''}`;
    const left = buildPick(row.home.team, row.home.line, row.home.odd, lkey);
    const right = buildPick(row.away.team, row.away.line, row.away.odd, rkey);

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

    const baseLine = row.rawLine || (row.over && row.over.line) || (row.under && row.under.line) || '';
    const lkey = `ou|${row.over.label || 'O'}|${baseLine}`;
    const rkey = `ou|${row.under.label || 'U'}|${baseLine}`;
    const left = buildPick(row.over.label, row.over.line, row.over.odd, lkey);
    const right = buildPick(row.under.label, row.under.line, row.under.odd, rkey);

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
  const hkey = `ml|home|${moneyLine.home.team || ''}`;
  const dkey = `ml|draw|${moneyLine.draw.label || ''}`;
  const akey = `ml|away|${moneyLine.away.team || ''}`;
  wrap.appendChild(buildPick(moneyLine.home.team, '', moneyLine.home.odd, hkey));
  wrap.appendChild(buildPick(moneyLine.draw.label, '', moneyLine.draw.odd, dkey));
  wrap.appendChild(buildPick(moneyLine.away.team, '', moneyLine.away.odd, akey));
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
  setText('stLastUpdate', '-');
  resetOddCache();
  clearSimple();
}

async function startStream() {
  stopStream();

  const memberId = getSelectedMemberId();
  if (!memberId) {
    setText('stStream', '请先选择会员');
    return;
  }

  const gid = $('inGid').value.trim();
  if (!gid) {
    setText('stStream', '请先输入 gid');
    return;
  }

  const refreshMs = Number($('inRefresh').value || 3000);
  const raw = $('ckRaw').checked ? '1' : '0';
  const confirm = $('ckConfirm').checked ? '1' : '0';

  // 新一轮监控：重置水位变化对比缓存（避免切换 gid 后错误高亮）
  resetOddCache();

  const url = `/api/chashui/stream?member_id=${encodeURIComponent(memberId)}&gid=${encodeURIComponent(gid)}&mode=${encodeURIComponent(currentMode)}&refreshMs=${encodeURIComponent(String(refreshMs))}&confirm=${confirm}&raw=${raw}`;
  stream = new EventSource(url);

  setText('stStream', '连接中…');

  stream.addEventListener('status', (e) => {
    try {
      const st = JSON.parse(e.data);
      const suffix = st.confirmOdds ? ' (已校验水位)' : '';
      const label = st.mode ? modeLabel(st.mode) : modeLabel(currentMode);
      setText('stStream', `已连接：${label} refresh=${st.refreshMs}ms${suffix}`);
    } catch {
      setText('stStream', '已连接');
    }
  });

  stream.addEventListener('data', (e) => {
    const payload = JSON.parse(e.data);
    renderMeta(payload.meta, payload.ftGids);
    renderSimple(payload.simple);
    setText('stLastUpdate', fmtNowTime());

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
  });
}

function bindUi() {
  $('btnRefreshStatus').addEventListener('click', async () => {
    await refreshStatus();
  });

  const btnPaste = $('btnPasteGid');
  if (btnPaste) {
    btnPaste.addEventListener('click', async () => {
      try {
        if (!navigator.clipboard || !navigator.clipboard.readText) {
          setText('stErr', '当前浏览器不支持剪贴板读取');
          return;
        }
        const txt = await navigator.clipboard.readText();
        const m = String(txt || '').match(/(\\d{6,})/);
        if (!m) {
          setText('stErr', '剪贴板未找到数字 gid');
          return;
        }
        $('inGid').value = m[1];
        setText('stErr', '-');
      } catch (e) {
        setText('stErr', `粘贴失败：${e && e.message ? e.message : String(e)}`);
      }
    });
  }

  $('selMember').addEventListener('change', async () => {
    stopStream();
    clearGamesTable();
    await refreshStatus();
  });

  const loadGames = async (mode) => {
    setText('stErr', '-');
    setMode(mode);

    const memberId = getSelectedMemberId();
    if (!memberId) {
      setText('stErr', '请先选择会员');
      clearGamesTable();
      return;
    }

    try {
      const data = await fetchJson(`/api/chashui/games?member_id=${encodeURIComponent(memberId)}&mode=${encodeURIComponent(mode)}`);
      lastGames = Array.isArray(data.games) ? data.games : [];
      const q = ($('inGameSearch') && $('inGameSearch').value) ? $('inGameSearch').value : '';
      const filtered = filterGames(lastGames, q);
      renderGames(filtered, lastGames.length);
    } catch (e) {
      setText('stErr', e.message || String(e));
      clearGamesTable();
    }
  };

  const inSearch = $('inGameSearch');
  if (inSearch) {
    inSearch.addEventListener('input', () => {
      const q = inSearch.value || '';
      const filtered = filterGames(lastGames, q);
      renderGames(filtered, lastGames.length);
    });
  }

  $('btnLoadGames').addEventListener('click', () => loadGames(currentMode));
  $('btnLoadLive').addEventListener('click', () => loadGames('live'));
  $('btnLoadToday').addEventListener('click', () => loadGames('today'));
  $('btnLoadEarly').addEventListener('click', () => loadGames('early'));

  $('btnStartStream').addEventListener('click', () => startStream());
  $('btnStopStream').addEventListener('click', () => stopStream());
}

async function main() {
  bindUi();
  setMode('live');

  await refreshStatus();
  statusTimer = setInterval(refreshStatus, 2000);

  // 默认隐藏 raw
  $('boxRawWrap').style.display = 'none';
}

main().catch((e) => {
  setText('stErr', e.message || String(e));
});
