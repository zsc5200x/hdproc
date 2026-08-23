let queueTimer = null;
let statusTimer = null;

let lastOddByKey = new Map();
let snapshotCache = new Map();
let snapshotInflight = new Map();

function createSemaphore(max) {
  let active = 0;
  const waiters = [];
  const release = () => {
    active = Math.max(0, active - 1);
    const next = waiters.shift();
    if (next) next();
  };
  return {
    async acquire() {
      if (active >= max) await new Promise((resolve) => waiters.push(resolve));
      active += 1;
      return release;
    },
  };
}

const snapshotSem = createSemaphore(4);

let cardStateByKey = new Map();
let lastRenderOrder = [];
let lastMembersSnapshot = [];

let dashboardQueueItems = [];
let dashboardTaskGroups = [];
let dashboardResults = [];
let dashboardCounts = {};
let dashboardSnapshotByGame = new Map();
let snapshotErrorByGame = new Map();
let activeTaskFilter = 'all';
let activeLogFilter = 'all';
let activeLogSplit = 'all';
let snapshotRevision = 0;
let lastQueueMeta = { updatedAt: '', serverTime: '' };
let queueRefreshInFlight = null;
let executionResultsRefreshInFlight = null;
const htmlRenderSignatures = new WeakMap();

const RECOMMENDED_CONFIG = {
  split_count: 3,
  split_delay_seconds: 0.5,
  wait_line_not_found_max_seconds: 45,
  wait_line_not_found_probe_seconds: 2,
  allow_quote_members: false,
};

let followSettingsCache = null;

const ERROR_MESSAGES = {
  ODDS_CHANGED: {
    title: '水位已变化',
    message: '会员端返回赔率跳动，系统正在刷新盘口后重试',
    solution: '建议：水位跳动频繁时，检查盘口刷新速度或减少分单次数',
    icon: 'fa-chart-line',
  },
  LINE_NOT_FOUND: {
    title: '盘口线位变化',
    message: '目标盘口线暂时找不到，正在持续探测中...',
    solution: '建议：延长探测窗口时间或减少分单数量',
    icon: 'fa-magnifying-glass',
  },
  GAME_NOT_FOUND: {
    title: '比赛已下架',
    message: '该比赛在会员端已找不到',
    solution: '可能原因：比赛已结束、被撤盘、或数据同步延迟',
    icon: 'fa-circle-xmark',
  },
  SCORE_CHANGED: {
    title: '比分已变化',
    message: '滚球比分已更新，护栏机制拒绝下注',
    solution: '建议：如需跟进球瞬间的单，请调整护栏配置',
    icon: 'fa-futbol',
  },
  AMOUNT_EXCEED: {
    title: '金额超限',
    message: '单笔金额超过会员限额',
    solution: '建议：增加分单数量或降低单笔金额',
    icon: 'fa-sack-dollar',
  },
  '1X001': {
    title: '盘口封盘',
    message: '当前盘口暂时封盘，系统正在按窗口持续探测',
    solution: '建议：等待开盘，或在配置里调整封盘等待窗口',
    icon: 'fa-hourglass-half',
  },
  '1X012': {
    title: '余额不足',
    message: '会员额度不足，当前账号无法继续下注',
    solution: '建议：切换可用会员或补充额度',
    icon: 'fa-ban',
  },
  UPSTREAM_502: {
    title: '上游网关 502',
    message: '盘口服务返回 Bad Gateway，当前请求没有完成',
    solution: '系统会退避后换会员重试，不会把这类网络故障当成盘口失败',
    icon: 'fa-arrow-up-right-from-square',
  },
  HARD_TIMEOUT: {
    title: '单次请求超时',
    message: '会员端请求超过单次执行时间上限',
    solution: '系统会释放当前会员并让其它排队任务先执行',
    icon: 'fa-clock',
  },
  READ_TIMEOUT: {
    title: '读取超时',
    message: '会员端在规定时间内没有返回结果',
    solution: '系统会退避后换会员重试；若已提交但结果不确定，会先核验历史注单',
    icon: 'fa-clock',
  },
  CONNECTION_ERROR: {
    title: '连接中断',
    message: '上游连接被远端关闭或中途断开',
    solution: '系统会清理连接并退避重试，避免连续占用同一会员',
    icon: 'fa-plug-circle-xmark',
  },
  HTTP_ERROR: {
    title: '上游请求失败',
    message: '会员端返回了 HTTP 层错误',
    solution: '系统会退避后重试，订单仍保留在队列中',
    icon: 'fa-triangle-exclamation',
  },
};

function $(id) {
  return document.getElementById(id);
}

function setText(id, text) {
  const el = $(id);
  if (!el) return;
  const nextText = text == null || text === '' ? '-' : String(text);
  if (el.textContent !== nextText) el.textContent = nextText;
}

function updateHtmlIfChanged(element, html, scrollContainer = element) {
  if (!element) return false;
  const nextHtml = String(html == null ? '' : html);
  if (htmlRenderSignatures.get(element) === nextHtml) return false;
  const scroller = scrollContainer || element;
  const scrollTop = Number(scroller && scroller.scrollTop);
  const scrollLeft = Number(scroller && scroller.scrollLeft);
  element.innerHTML = nextHtml;
  htmlRenderSignatures.set(element, nextHtml);
  if (scroller) {
    if (Number.isFinite(scrollTop)) scroller.scrollTop = scrollTop;
    if (Number.isFinite(scrollLeft)) scroller.scrollLeft = scrollLeft;
  }
  return true;
}

function displayExecutionMessage(value) {
  const message = String(value == null ? '' : value).trim();
  if (/会员端预校验失败/.test(message)) return '历史旧链执行失败（重构前记录）';
  return message;
}

function formatErrorMessage(text) {
  const raw = text == null || text === '' ? '-' : String(text).trim();
  if (!raw || raw === '-') return '-';
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return '连接后台失败：请确认 WebUI 服务正在运行，稍后刷新。';
  }
  const http = raw.match(/^HTTP\s+(\d+)/i);
  if (http) return `请求失败（HTTP ${http[1]}）`;
  return raw;
}

function setErrBox(text) {
  const el = $('stErr');
  if (!el) return;
  const raw = text == null || text === '' ? '-' : String(text);
  const isOk = raw === '-' || raw.trim() === '';
  const nextText = isOk ? '运行正常' : formatErrorMessage(raw);
  if (el.textContent !== nextText) el.textContent = nextText;
  try {
    el.classList.toggle('isErr', !isOk);
  } catch {}
}

function toCount(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.floor(n);
}

function fmtNowTime() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

async function fetchJson(url, options) {
  let res;
  try {
    res = await fetch(url, options);
  } catch {
    throw new Error('连接后台失败：请确认 WebUI 服务正在运行，稍后刷新。');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = data && (data.message || data.error) ? data.message || data.error : `请求失败（HTTP ${res.status}）`;
    throw new Error(msg);
  }
  return data;
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

function resetOddCache() {
  lastOddByKey = new Map();
}

function getSelectedMemberId() {
  const sel = $('selMember');
  return sel && sel.value ? String(sel.value) : '';
}

function formatMemberRole(role) {
  const r = String(role || '').trim().toLowerCase();
  if (!r) return '';
  if (r === 'quote') return '引用';
  if (r === 'follow') return '跟单';
  if (r === 'member') return '会员';
  return String(role || '').trim();
}

function formatMemberStatus(status) {
  const s = String(status || '').trim().toLowerCase();
  if (!s) return '';
  if (s === 'online') return '在线';
  if (s === 'offline') return '离线';
  if (s === 'unknown') return '未知';
  if (s === 'error') return '异常';
  return String(status || '').trim();
}

function memberUsage(m) {
  if (!m || typeof m !== 'object') return 'bet';
  const usage = String(m.usage || '').trim().toLowerCase();
  if (['read', 'quote', 'data', 'readonly'].includes(usage)) return 'read';
  const role = String(m.role || '').trim().toLowerCase();
  return role === 'quote' ? 'read' : 'bet';
}

function isBetExecutionMember(m) {
  return memberUsage(m) === 'bet';
}

function memberCanBet(m) {
  if (!m || typeof m !== 'object') return false;
  const status = String(m.status || '').trim().toLowerCase();
  if (status !== 'online') return false;
  return isBetExecutionMember(m);
}

function getAvailableBetMemberCount() {
  if (!Array.isArray(lastMembersSnapshot) || lastMembersSnapshot.length === 0) return 0;
  let n = 0;
  for (const m of lastMembersSnapshot) {
    if (memberCanBet(m)) n += 1;
  }
  return n;
}

function refreshAvailableMemberChips() {
  const n = getAvailableBetMemberCount();
  for (const st of cardStateByKey.values()) {
    if (!st || !st.refs || !st.refs.metaAvailableMembers) continue;
    st.refs.metaAvailableMembers.textContent = `${n} 个`;
  }
}

function renderMembers(members, selectedId) {
  const sel = $('selMember');
  if (!sel) return;

  const old = sel.value;
  const keep = selectedId || old;
  const options = ['<option value="">请选择查水账号</option>'];
  for (const m of members || []) {
    const value = String(m.id || '');
    const label = m.label || m.username || m.id;
    const role = formatMemberRole(m.role);
    const st = formatMemberStatus(m.status);
    const roleText = role ? `【${role}】` : '';
    const stText = st ? `（${st}）` : '';
    options.push(`<option value="${escapeHtml(value)}">${escapeHtml(`${label}${roleText}${stText}`)}</option>`);
  }

  updateHtmlIfChanged(sel, options.join(''), null);
  if (keep) sel.value = keep;
}

async function refreshMemberStatus() {
  try {
    const mid = getSelectedMemberId();
    const url = mid ? `/api/chashui/status?member_id=${encodeURIComponent(mid)}` : '/api/chashui/status';
    const [arsenal, st] = await Promise.all([fetchJson('/api/arsenal'), fetchJson(url)]);
    const allMembers = Array.isArray(arsenal.members)
      ? arsenal.members
      : Array.isArray(st.members)
        ? st.members
        : [];
    lastMembersSnapshot = allMembers.slice();
    renderMembers(allMembers, st.member && st.member.id ? String(st.member.id) : mid);
    refreshAvailableMemberChips();
    if (st.member) {
      const label = st.member.label || st.member.username || st.member.id || '-';
      const role = formatMemberRole(st.member.role);
      const status = formatMemberStatus(st.member.status);
      const roleText = role ? `【${role}】` : '';
      const stText = status ? `（${status}）` : '';
      setText('stQuoteMember', `查水账号：${label}${roleText}${stText}`);
    }
    if (st.server_time) setText('stServerTime', `服务器时间：${st.server_time}`);
    renderMemberPool();
  } catch (e) {
    setErrBox(e.message || String(e));
  }
}

function readNumber(id, fallback) {
  const el = $(id);
  if (!el) return fallback;
  const n = Number(el.value);
  if (!Number.isFinite(n)) return fallback;
  return n;
}

function parseAmount(v) {
  if (v == null) return null;
  const s = String(v).replace(/,/g, '').trim();
  if (!s) return null;
  const n = Number.parseFloat(s);
  if (!Number.isFinite(n)) return null;
  return n;
}

function formatAmount(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '-';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function getFollowTotalAmount(order, job) {
  const orderStake = parseAmount(order && order.stake);
  const jobStake = parseAmount(job && job.stake);
  const splitStakes = Array.isArray(job && job.split_stakes) ? job.split_stakes : [];

  if (jobStake != null) return jobStake;
  if (orderStake != null) return orderStake;

  let sum = 0;
  let has = false;
  for (const x of splitStakes) {
    const n = parseAmount(x);
    if (n == null) continue;
    sum += n;
    has = true;
  }
  return has ? sum : null;
}

function getFollowedAmount(job, totalAmount) {
  if (!job || typeof job !== 'object') return 0;
  const splitTickets = Array.isArray(job.split_tickets) ? job.split_tickets : [];
  const splitStakes = Array.isArray(job.split_stakes) ? job.split_stakes : [];
  let sum = 0;
  let hit = 0;

  for (let i = 0; i < splitTickets.length; i += 1) {
    if (!splitTickets[i]) continue;
    const n = parseAmount(splitStakes[i]);
    if (n == null) continue;
    sum += n;
    hit += 1;
  }

  if (hit > 0) return sum;

  const count = splitTickets.filter(Boolean).length;
  const total = Number(totalAmount);
  const splitCount = Number(job.split_count || 0);
  if (count > 0 && Number.isFinite(total) && total >= 0 && Number.isFinite(splitCount) && splitCount > 0) {
    return (total * count) / splitCount;
  }
  return 0;
}

function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function extractErrorCode(job) {
  if (!job || typeof job !== 'object') return '';
  let code = String(job.last_err || '').trim();
  if (!code) {
    const msg = String(job.last_message || '');
    const m = msg.match(/err=([A-Za-z0-9_]+)/i);
    if (m) code = String(m[1] || '').trim();
  }
  const up = String(code || '').toUpperCase();
  if (!up) return '';
  if (up === '1X003' || up === '1X015') return 'LINE_NOT_FOUND';
  if (up === 'BETERROR' || up.startsWith('BETERROR')) return 'ODDS_CHANGED';
  return up;
}

function formatError(errorCode, details) {
  const info = ERROR_MESSAGES[errorCode] || {
    title: '未知错误',
    message: details || '系统返回了未识别的错误',
    solution: '请查看日志获取详细信息',
    icon: 'fa-triangle-exclamation',
  };
  const detailsText = String(details || '').trim();
  const detailsBlock = detailsText ? `<div class="errorDetails">${escapeHtml(detailsText)}</div>` : '';
  return `
    <div class="errorCard">
      <div class="errorIcon"><i class="fa-solid ${escapeHtml(info.icon)}" aria-hidden="true"></i></div>
      <div class="errorContent">
        <div class="errorTitle">${escapeHtml(info.title)}</div>
        <div class="errorMessage">${escapeHtml(info.message)}</div>
        <div class="errorSolution">${escapeHtml(info.solution)}</div>
        ${detailsBlock}
      </div>
    </div>
  `;
}

function readBool(id, fallback) {
  const el = $(id);
  if (!el) return Boolean(fallback);
  return Boolean(el.checked);
}

function setConfigStatus(text, cls) {
  const el = $('stConfigStatus');
  if (!el) return;
  el.textContent = text && String(text).trim() ? String(text) : '未修改';
  try {
    el.classList.remove('ok', 'warn', 'dirty');
    if (cls) el.classList.add(cls);
  } catch {}
}

function normalizeFollowSettings(doc) {
  const src = doc && typeof doc === 'object' ? doc : {};
  return {
    split_count: Math.max(1, Math.min(5, Number(src.split_count || RECOMMENDED_CONFIG.split_count))),
    split_delay_seconds: Math.max(0, Math.min(10, Number(src.split_delay_seconds || RECOMMENDED_CONFIG.split_delay_seconds))),
    wait_line_not_found_max_seconds: Math.max(
      10,
      Math.min(120, Number(src.wait_line_not_found_max_seconds || RECOMMENDED_CONFIG.wait_line_not_found_max_seconds))
    ),
    wait_line_not_found_probe_seconds: Math.max(
      1,
      Math.min(10, Number(src.wait_line_not_found_probe_seconds || RECOMMENDED_CONFIG.wait_line_not_found_probe_seconds))
    ),
    allow_quote_members:
      typeof src.allow_quote_members === 'boolean' ? src.allow_quote_members : RECOMMENDED_CONFIG.allow_quote_members,
  };
}

function applyConfigToForm(cfg) {
  const c = normalizeFollowSettings(cfg);
  if ($('inSplitCount')) $('inSplitCount').value = String(c.split_count);
  if ($('inSplitDelay')) $('inSplitDelay').value = String(c.split_delay_seconds);
  if ($('inWaitLineNotFound')) $('inWaitLineNotFound').value = String(c.wait_line_not_found_max_seconds);
}

function collectConfigFromForm() {
  return normalizeFollowSettings({
    split_count: readNumber('inSplitCount', RECOMMENDED_CONFIG.split_count),
    split_delay_seconds: readNumber('inSplitDelay', RECOMMENDED_CONFIG.split_delay_seconds),
    wait_line_not_found_max_seconds: readNumber('inWaitLineNotFound', RECOMMENDED_CONFIG.wait_line_not_found_max_seconds),
    wait_line_not_found_probe_seconds: RECOMMENDED_CONFIG.wait_line_not_found_probe_seconds,
    allow_quote_members: RECOMMENDED_CONFIG.allow_quote_members,
  });
}

function configEquals(a, b) {
  const x = normalizeFollowSettings(a);
  const y = normalizeFollowSettings(b);
  return (
    x.split_count === y.split_count &&
    Math.abs(x.split_delay_seconds - y.split_delay_seconds) < 1e-9 &&
    x.wait_line_not_found_max_seconds === y.wait_line_not_found_max_seconds &&
    x.wait_line_not_found_probe_seconds === y.wait_line_not_found_probe_seconds &&
    x.allow_quote_members === y.allow_quote_members
  );
}

function updateConfigDirtyState() {
  const cur = collectConfigFromForm();
  if (!followSettingsCache) {
    setConfigStatus('未加载', 'warn');
    return;
  }
  if (configEquals(cur, followSettingsCache)) {
    setConfigStatus('已同步', 'ok');
    return;
  }
  setConfigStatus('有未保存修改', 'dirty');
}

async function loadFollowSettings() {
  try {
    const data = await fetchJson('/api/follow-settings');
    const settings = data && data.settings ? data.settings : {};
    followSettingsCache = normalizeFollowSettings(settings);
    applyConfigToForm(followSettingsCache);
    setConfigStatus('已同步', 'ok');
  } catch (e) {
    setConfigStatus('加载失败', 'warn');
    setErrBox(e.message || String(e));
  }
}

async function saveFollowSettings() {
  const payload = collectConfigFromForm();
  try {
    const data = await fetchJson('/api/follow-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: payload }),
    });
    followSettingsCache = normalizeFollowSettings(data && data.settings ? data.settings : payload);
    applyConfigToForm(followSettingsCache);
    setConfigStatus('保存成功', 'ok');
    setErrBox('-');
  } catch (e) {
    setConfigStatus('保存失败', 'warn');
    setErrBox(e.message || String(e));
  }
}

function resetFollowSettingsToRecommended() {
  applyConfigToForm(RECOMMENDED_CONFIG);
  updateConfigDirtyState();
}

function baseWtype(wt) {
  const s = String(wt || '').trim().toUpperCase();
  if (!s) return '';
  if (s.length > 1 && /^[A-F]/.test(s[0])) {
    return s.slice(1);
  }
  return s;
}

function snapshotMarketWtype(wt) {
  const base = baseWtype(wt);
  if (base === 'OU' || base === 'ROU' || base === 'FOU') return 'OU';
  if (base === 'HOU' || base === 'HROU' || base === 'HFOU') return 'HOU';
  return base;
}

function parseSplitLineAvg(s) {
  const text = String(s || '').replace(/\\\//g, '/').trim();
  if (!text) return null;
  if (!text.includes('/')) {
    const n = Number.parseFloat(text);
    return Number.isFinite(n) ? Math.abs(n) : null;
  }
  const parts = text
    .split('/')
    .map((x) => x.trim())
    .filter(Boolean);
  const nums = [];
  for (const p of parts.slice(0, 2)) {
    const n = Number.parseFloat(p);
    if (Number.isFinite(n)) nums.push(Math.abs(n));
  }
  if (!nums.length) return null;
  if (nums.length === 1) return nums[0];
  return (nums[0] + nums[1]) / 2;
}

function formatSnapshotError(error) {
  const raw = String(error == null ? '' : error).trim();
  if (!raw) return '快照请求失败';
  if (/game_not_found/i.test(raw)) return '比赛已不在当前盘口列表';
  if (/readonly_pool_empty/i.test(raw)) return '没有可用查水会员';
  if (/member_missing_uid_cookie_ver|session_incomplete/i.test(raw)) return '查水会员会话不完整';
  if (/timeout|timed out|hard_timeout/i.test(raw)) return '查水请求超时';
  return raw;
}

function parseSignedSplitLineFromText(text) {
  const s = String(text || '').replace(/\\\//g, '/');
  if (!s) return null;
  const re = /([+-]?\d+(?:\.\d+)?)(?:\s*\/\s*([+-]?\d+(?:\.\d+)?))?/g;
  let best = null;
  let m;
  while ((m = re.exec(s)) !== null) {
    if (m[2] == null) continue;
    best = [m[1], m[2]];
    break;
  }
  if (!best) {
    const m1 = s.match(/([+-]?\d+(?:\.\d+)?)\s*$/);
    if (m1) {
      const n = Number.parseFloat(m1[1]);
      if (Number.isFinite(n)) return n;
    }
    const m2 = s.match(/([+-]\d+(?:\.\d+)?)/);
    if (m2) {
      const n = Number.parseFloat(m2[1]);
      if (Number.isFinite(n)) return n;
    }
    return null;
  }
  let aVal = Number.parseFloat(best[0]);
  let bVal = Number.parseFloat(best[1]);
  if (!Number.isFinite(aVal) || !Number.isFinite(bVal)) return null;
  let sign1 = null;
  const aStr = String(best[0]).trim();
  if (aStr.startsWith('-')) sign1 = -1;
  else if (aStr.startsWith('+')) sign1 = 1;
  const bStr = String(best[1]).trim();
  if (sign1 != null && !(bStr.startsWith('-') || bStr.startsWith('+'))) {
    bVal = sign1 * Math.abs(bVal);
  }
  return (aVal + bVal) / 2;
}

function buildPick({ label, line, odd, oddKey, isTarget }) {
  const pick = document.createElement('div');
  pick.className = isTarget ? 'pick targetPick' : 'pick';

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
  decorateOdd(oddEl, oddKey, odd);

  pick.appendChild(top);
  pick.appendChild(oddEl);
  return pick;
}

function renderHandicap(box, lines, target, cardKey, sideOnly = false) {
  box.innerHTML = '';
  if (!Array.isArray(lines) || lines.length === 0) {
    box.innerHTML = '<div class="hint">暂无让球盘口</div>';
    return;
  }
  const wantWtype = baseWtype(target && target.wtype);
  const wantLine = target && target.target_line_val != null ? Number(target.target_line_val) : null;
  const wantSide = String(target && target.chose_team ? target.chose_team : '').toUpperCase();

  for (const row of lines) {
    const wrap = document.createElement('div');
    wrap.className = 'pairRow';

    const rowWtype = baseWtype(row.wtype);
    const homeLineVal = parseSignedSplitLineFromText(row.home && row.home.line);
    const awayLineVal = parseSignedSplitLineFromText(row.away && row.away.line);

    const hitRow = wantWtype && rowWtype && wantWtype === rowWtype && wantLine != null;
    const hitHome = hitRow && wantSide === 'H' && homeLineVal != null && Math.abs(homeLineVal - wantLine) < 1e-6;
    const hitAway = hitRow && wantSide === 'C' && awayLineVal != null && Math.abs(awayLineVal - wantLine) < 1e-6;

    const lkey = `${cardKey}|hc|H|${rowWtype}|${row.home.line || ''}`;
    const rkey = `${cardKey}|hc|C|${rowWtype}|${row.away.line || ''}`;

    if (!sideOnly || wantSide === 'H') {
      wrap.appendChild(
        buildPick({
          label: row.home.team,
          line: row.home.line,
          odd: row.home.odd,
          oddKey: lkey,
          isTarget: hitHome,
        })
      );
    }
    if (!sideOnly || wantSide === 'C') {
      wrap.appendChild(
        buildPick({
          label: row.away.team,
          line: row.away.line,
          odd: row.away.odd,
          oddKey: rkey,
          isTarget: hitAway,
        })
      );
    }
    if (!wrap.children.length) continue;
    box.appendChild(wrap);
  }
}

function renderOverUnder(box, lines, target, cardKey) {
  box.innerHTML = '';
  if (!Array.isArray(lines) || lines.length === 0) {
    box.innerHTML = '<div class="hint">暂无大小盘口</div>';
    return;
  }
  const wantWtype = baseWtype(target && target.wtype);
  const wantLine = target && target.target_line_val != null ? Number(target.target_line_val) : null;
  const isOver = target && target.is_over != null ? Boolean(target.is_over) : null;

  for (const row of lines) {
    const wrap = document.createElement('div');
    const singleSide = isOver === true || isOver === false;
    wrap.className = singleSide ? 'pairRow singleRow' : 'pairRow';

    const rowWtype = baseWtype(row.wtype);
    const baseLine = row.rawLine || (row.over && row.over.line) || (row.under && row.under.line) || '';
    const rowVal = parseSplitLineAvg(baseLine);

    const hitRow = wantWtype && rowWtype && wantWtype === rowWtype && wantLine != null && rowVal != null && Math.abs(rowVal - wantLine) < 1e-6;
    const hitOver = hitRow && isOver === true;
    const hitUnder = hitRow && isOver === false;

    const lkey = `${cardKey}|ou|O|${rowWtype}|${baseLine}`;
    const rkey = `${cardKey}|ou|U|${rowWtype}|${baseLine}`;

    if (isOver !== false) {
      wrap.appendChild(
        buildPick({
          label: row.over.label,
          line: row.over.line,
          odd: row.over.odd,
          oddKey: lkey,
          isTarget: hitOver,
        })
      );
    }
    if (isOver !== true) {
      wrap.appendChild(
        buildPick({
          label: row.under.label,
          line: row.under.line,
          odd: row.under.odd,
          oddKey: rkey,
          isTarget: hitUnder,
        })
      );
    }
    box.appendChild(wrap);
  }
}

function renderMoneyLine(box, moneyLine, target, cardKey) {
  box.innerHTML = '';
  if (!moneyLine || moneyLine.empty) {
    box.innerHTML = '<div class="hint">暂无独赢盘口</div>';
    return;
  }

  const wantWtype = baseWtype(target && target.wtype);
  const wantSide = String(target && target.chose_team ? target.chose_team : '').toUpperCase();
  const isMl = wantWtype && wantWtype.endsWith('M');

  const wrap = document.createElement('div');
  wrap.className = 'tripleRow';

  wrap.appendChild(
    buildPick({
      label: moneyLine.home.team,
      line: '',
      odd: moneyLine.home.odd,
      oddKey: `${cardKey}|ml|H`,
      isTarget: isMl && wantSide === 'H',
    })
  );
  wrap.appendChild(
    buildPick({
      label: moneyLine.draw.label,
      line: '',
      odd: moneyLine.draw.odd,
      oddKey: `${cardKey}|ml|N`,
      isTarget: isMl && wantSide === 'N',
    })
  );
  wrap.appendChild(
    buildPick({
      label: moneyLine.away.team,
      line: '',
      odd: moneyLine.away.odd,
      oddKey: `${cardKey}|ml|C`,
      isTarget: isMl && wantSide === 'C',
    })
  );
  box.appendChild(wrap);
}

function renderFollowMarketSnapshot(box, order, derived, simple, cardKey) {
  if (!box) return;
  box.innerHTML = '';

  const original = document.createElement('div');
  original.className = 'followOriginal';
  original.textContent = `原单：${formatFollowMarketText(order)}`;
  box.appendChild(original);

  if (!simple || typeof simple !== 'object') return;

  const label = document.createElement('div');
  label.className = 'followCurrentLabel';
  label.textContent = '当前盘口';
  box.appendChild(label);

  const lines = document.createElement('div');
  lines.className = 'followSnapshotLines lines';
  const wantWtype = baseWtype(derived && derived.wtype);
  if (wantWtype.endsWith('OU')) {
    renderOverUnder(lines, simple.overUnder && simple.overUnder.lines, derived, cardKey);
  } else if (wantWtype.endsWith('RE') || wantWtype.endsWith('R')) {
    renderHandicap(lines, simple.handicap && simple.handicap.lines, derived, cardKey, true);
  } else if (wantWtype.endsWith('M')) {
    renderMoneyLine(lines, simple.moneyLine, derived, cardKey);
  } else {
    lines.innerHTML = '<div class="hint">当前玩法暂无盘口快照</div>';
  }
  box.appendChild(lines);
}

async function requestFollowSnapshot({ memberId, gid, mode }) {
  const cacheKey = `${memberId}|${mode}|${gid}`;
  const now = Date.now();
  const cached = snapshotCache.get(cacheKey);
  if (cached && now - cached.ts < 2200) return cached.data;
  if (snapshotInflight.has(cacheKey)) return snapshotInflight.get(cacheKey);

  const url = `/api/chashui/odds?member_id=${encodeURIComponent(memberId)}&gid=${encodeURIComponent(gid)}&mode=${encodeURIComponent(mode)}&confirm=0`;
  const request = (async () => {
    const release = await snapshotSem.acquire();
    try {
      const data = await fetchJson(url);
      snapshotCache.set(cacheKey, { ts: Date.now(), data });
      return data;
    } finally {
      release();
    }
  })()
    .finally(() => snapshotInflight.delete(cacheKey));
  snapshotInflight.set(cacheKey, request);
  return request;
}

function formatTs(ts) {
  if (!ts) return '';
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return String(ts);
  const d = new Date(n * 1000);
  const pad = (x) => String(x).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatCountdown(tsSec) {
  const n = Number(tsSec);
  if (!Number.isFinite(n) || n <= 0) return '';
  const now = Date.now() / 1000;
  const left = Math.max(0, n - now);
  if (left < 0.5) return '马上';
  if (left < 60) return `${left.toFixed(0)}秒`;
  const m = Math.floor(left / 60);
  const s = Math.floor(left % 60);
  return `${m}分${s}秒`;
}

function shouldShowItem(item, q) {
  const query = String(q || '').trim().toLowerCase();
  if (!query) return true;
  const order = (item && item.order) || {};
  const d = (order && order.derived) || {};
  const hay = [
    item.key,
    item.status,
    order.target,
    order.orderId,
    order.gid,
    order.league,
    order.sides,
    order.betType,
    order.line,
    d.wtype,
    d.target_line_txt,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return hay.includes(query);
}

function buildMiniKvRow(k, v, mono) {
  const frag = document.createDocumentFragment();
  const a = document.createElement('span');
  a.textContent = k;
  const b = document.createElement('span');
  if (mono) b.className = 'mono';
  b.textContent = v == null || v === '' ? '-' : String(v);
  frag.appendChild(a);
  frag.appendChild(b);
  return frag;
}

function makeKvRow(label, mono) {
  const row = document.createElement('div');
  const a = document.createElement('span');
  a.textContent = label;
  const b = document.createElement('span');
  if (mono) b.className = 'mono';
  b.textContent = '-';
  row.appendChild(a);
  row.appendChild(b);
  return { row, valueEl: b };
}

function makeMetaItem(label, { mono, span } = {}) {
  const item = document.createElement('div');
  item.className = `metaItem${span ? ` span${span}` : ''}`;
  const a = document.createElement('div');
  a.className = 'metaLabel';
  a.textContent = label;
  const b = document.createElement('div');
  b.className = mono ? 'metaValue mono' : 'metaValue';
  b.textContent = '-';
  item.appendChild(a);
  item.appendChild(b);
  return { item, valueEl: b };
}

function stripParens(text) {
  const s = String(text || '').trim();
  if (!s) return '';
  return s
    .replace(/（/g, '(')
    .replace(/）/g, ')')
    .replace(/\([^)]*\)/g, '')
    .replace(/\s+vs\s+/gi, ' 对 ')
    .replace(/\s+/g, ' ')
    .trim();
}

function formatModeLabel(mode) {
  const m = String(mode || '').trim().toLowerCase();
  if (!m) return '';
  if (m === 'live') return '滚球';
  if (m === 'today') return '今日';
  if (m === 'early') return '早盘';
  return String(mode || '').trim();
}

function cleanBetTypeText(text) {
  const s = String(text || '').trim();
  if (!s) return '';
  return s
    .replace(/[（(]\s*滚球\s*[）)]/g, '')
    .replace(/[（(]\s*今日\s*[）)]/g, '')
    .replace(/[（(]\s*早盘\s*[）)]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function formatBetText(order) {
  const bt = cleanBetTypeText(order && order.betType ? String(order.betType) : '');
  const line = order && order.line ? String(order.line).trim() : '';
  return `${bt} ${line}`.trim();
}

function formatFollowMarketText(order) {
  const market = formatBetText(order);
  const odds = order && order.odds != null ? String(order.odds).trim() : '';
  if (market && odds) return `${market} @ ${odds}`;
  return market || (odds ? `@ ${odds}` : '-');
}

function formatMatchTitle(order) {
  const sides = order && order.sides ? stripParens(order.sides) : '';
  if (sides) return sides;
  const league = order && order.league ? String(order.league).trim() : '';
  if (league) return league;
  return '未命名比赛';
}

function formatQueueStage(status) {
  const s = String(status || '').trim().toLowerCase();
  if (s === 'job') return '队列中';
  if (s === 'pending') return '待入队';
  if (s === 'done') return '已完成';
  if (s === 'failed' || s === 'fail' || s === 'error') return '失败';
  if (s === 'canceled' || s === 'cancelled' || s === 'cancel') return '已取消';
  return s ? '未知' : '-';
}

function buildSubLine(order, derived) {
  const subParts = [];
  const league = order && order.league ? String(order.league).trim() : '';
  if (league) subParts.push(league);
  const mode = formatModeLabel(derived && derived.mode);
  if (mode) subParts.push(mode);
  const bet = formatBetText(order);
  if (bet) subParts.push(bet);
  return subParts.join(' · ') || '-';
}

const SPLIT_ORDER_NUMERALS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];

function getSplitOrderName(index) {
  const n = Math.max(0, Number(index) || 0);
  return n < SPLIT_ORDER_NUMERALS.length
    ? `拆分单${SPLIT_ORDER_NUMERALS[n]}`
    : `拆分单 ${n + 1}`;
}

function normalizeSplitStatus(rawStatus) {
  const raw = String(rawStatus || '').trim().toLowerCase();
  if (['failed', 'fail', 'error', 'skipped', 'canceled', 'cancelled'].includes(raw)) return 'failed';
  if (['done', 'success', 'succeeded', 'ok'].includes(raw)) return 'success';
  if (['running', 'executing', 'in_progress'].includes(raw)) return 'running';
  if (['retry', 'retrying', 'cooldown'].includes(raw)) return 'retry';
  if (['waiting', 'pending', 'queued', 'job'].includes(raw)) return 'waiting';
  return '';
}

function deriveOrderStatus(tasks, itemStatus) {
  const statuses = (tasks || []).map((task) => String(task && task.status || 'waiting'));
  const success = statuses.filter((status) => status === 'success').length;
  const failed = statuses.filter((status) => status === 'failed').length;
  const running = statuses.includes('running');
  const retry = statuses.includes('retry');
  const waiting = statuses.includes('waiting');
  if (success > 0 && failed > 0) return 'partial';
  if (success === statuses.length && statuses.length > 0) return 'success';
  if (failed === statuses.length && statuses.length > 0) return 'failed';
  if (running) return 'running';
  if (retry) return 'retry';
  if (waiting) return 'waiting';
  return normalizeSplitStatus(itemStatus) || 'waiting';
}

function formatOrderDateTime(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return '-';
  const date = new Date(n < 1e12 ? n * 1000 : n);
  if (Number.isNaN(date.getTime())) return '-';
  const pad = (part) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function resolveFailureReason(task) {
  const raw = displayExecutionMessage(task && (task.failureReason || task.message || task.errorCode));
  if (!raw) return '';
  const code = String(task && task.errorCode || '').toUpperCase();
  if (code && ERROR_MESSAGES[code]) return ERROR_MESSAGES[code].title;
  if (/重试次数已达上限|retry.?limit|max.?retry/i.test(raw)) return '重试次数已上限';
  if (/余额|额度|balance|credit/i.test(raw)) return '余额不足';
  if (/盘口不存在|盘口不可用|line.?not.?found|game.?not.?found/i.test(raw)) return '盘口不存在';
  if (/变化|odds|beterror/i.test(raw)) return '盘口已变化';
  if (/掉线|offline|登录|session/i.test(raw)) return '会员掉线';
  return raw;
}

function normalizeTaskStatus(itemStatus, task, terminalOk) {
  const raw = String(itemStatus || '').trim().toLowerCase();
  if (['failed', 'fail', 'error', 'skipped', 'canceled', 'cancelled'].includes(raw)) return 'failed';
  if (terminalOk === true || raw === 'done' || raw === 'success' || raw === 'ok') return 'success';
  const err = String((task && (task.last_err || task.last_message)) || '').trim();
  const attempts = toCount(task && task.attempts);
  if (err || attempts > 0 && !(task && task.member_id)) return 'retry';
  if (task && task.member_id) return 'running';
  return 'waiting';
}

function latestSplitResult(orderId, splitIndex, splitCount) {
  const rows = (dashboardResults || []).filter((row) => String(row && row.orderId || '').trim() === String(orderId || '').trim());
  if (!rows.length) return null;
  const indexed = rows
    .map((row) => ({ row, index: Number(row && row.split_index) }))
    .filter((item) => Number.isFinite(item.index));
  if (!indexed.length) return null;
  const hasZeroIndex = indexed.some((item) => item.index === 0);
  const oneBased = !hasZeroIndex && indexed.some((item) => item.index === Number(splitCount));
  const wanted = oneBased ? Number(splitIndex) + 1 : Number(splitIndex);
  const matches = indexed.filter((item) => item.index === wanted).map((item) => item.row);
  if (!matches.length) return null;
  return matches.sort((a, b) => Number(b.ts_ms || b.ts || 0) - Number(a.ts_ms || a.ts || 0))[0] || null;
}

function buildExecutionTaskModels(items) {
  const groups = [];
  for (const item of items || []) {
    if (!item || typeof item !== 'object') continue;
    const order = item.order && typeof item.order === 'object' ? item.order : {};
    const job = item.job && typeof item.job === 'object' ? item.job : {};
    const storedSplitTasks = Array.isArray(job.split_tasks) ? job.split_tasks.filter((task) => task && typeof task === 'object') : [];
    const splitStakes = Array.isArray(job.split_stakes) ? job.split_stakes : [];
    const splitTickets = Array.isArray(job.split_tickets) ? job.split_tickets : [];
    const itemStatus = String(item.status || '').toLowerCase();
    const splitDone = toCount(job.split_done);
    const declaredSplitCount = Math.max(
      1,
      toCount(job.split_count) || toCount(job.split_total) || storedSplitTasks.length || splitStakes.length || splitTickets.length || 1
    );
    const splitTasks = storedSplitTasks.length
      ? storedSplitTasks
      : declaredSplitCount > 1
        ? Array.from({ length: declaredSplitCount }, (_, index) => ({ split_index: index }))
        : [job];
    const isTerminalSuccess = itemStatus === 'done' || (Boolean(job.ticket_id) && declaredSplitCount <= 1);
    const fallbackAmount = parseAmount(job.stake != null ? job.stake : order.stake);
    const splitCount = Math.max(1, declaredSplitCount, splitTasks.length);
    const tasks = splitTasks.map((rawTask, index) => {
      const task = rawTask && typeof rawTask === 'object' ? rawTask : {};
      const splitIndex = Number.isFinite(Number(task.split_index)) ? Number(task.split_index) : index;
      const result = latestSplitResult(order.orderId || job.order_id, splitIndex, splitCount);
      const taskMemberId = String(task.member_id || job.member_id || (result && result.member_id) || '').trim();
      const amountFromTask = parseAmount(task.stake);
      const amountFromParts = parseAmount(splitStakes[splitIndex]);
      const amountFromResult = parseAmount(result && result.stake);
      const amount = amountFromParts != null
        ? amountFromParts
        : amountFromTask != null
          ? amountFromTask
          : amountFromResult != null
            ? amountFromResult
          : splitCount <= 1 && fallbackAmount != null
            ? fallbackAmount / splitCount
            : null;
      const ticket = task.ticket_id || splitTickets[splitIndex] || (result && result.ticket_id) || job.ticket_id || '';
      const taskStatus = normalizeSplitStatus(task.status);
      let status = taskStatus || normalizeTaskStatus(itemStatus, task, Boolean(ticket) || isTerminalSuccess);
      if (!taskStatus && result && result.ok === true) status = 'success';
      if (!taskStatus && result && result.ok === false && status === 'waiting') status = 'retry';
      if (!taskStatus && !storedSplitTasks.length && declaredSplitCount > 1 && itemStatus === 'failed') {
        status = splitDone > splitIndex ? 'success' : 'failed';
      }
      if (!taskStatus && !storedSplitTasks.length && declaredSplitCount > 1 && itemStatus === 'done') {
        status = splitDone > 0 && splitDone <= splitIndex ? 'waiting' : 'success';
      }
      const failureReason = displayExecutionMessage(task.last_message || task.last_err || (result && (result.message || result.error_type)) || job.reason || job.last_message || order.last_result && (order.last_result.message || order.last_result.error_type));
      const errorCode = extractErrorCode({ last_err: task.last_err || job.last_err, last_message: failureReason });
      const resultOdd = result && (result.ioratio || result.bet_result_head && result.bet_result_head.ioratio);
      return {
        key: `${String(item.key || order.orderId || 'order')}#task:${splitIndex}`,
        orderKey: String(item.key || ''),
        orderId: String(order.orderId || job.order_id || '-'),
        index: splitIndex,
        splitCount,
        label: getSplitOrderName(splitIndex),
        status,
        memberId: taskMemberId,
        amount: amount == null ? null : Math.round(Number(amount) * 100) / 100,
        attempts: toCount(task.attempts != null && Number(task.attempts) > 0 ? task.attempts : result && result.attempt != null ? result.attempt : job.attempts),
        message: failureReason,
        failureReason,
        errorCode,
        ticket: String(ticket || ''),
        match: formatMatchTitle(order),
        market: formatFollowMarketText(order),
        sourceOdd: resultOdd != null ? String(resultOdd) : order.odds == null ? '' : String(order.odds),
        gid: String(order.gid || (order.derived && order.derived.gid) || ''),
        mode: String((order.derived && order.derived.mode) || 'live'),
        derived: order.derived || {},
        order,
      };
    });
    groups.push({
      key: String(item.key || order.orderId || `order-${groups.length + 1}`),
      orderId: String(order.orderId || job.order_id || '-'),
      match: formatMatchTitle(order),
      league: String(order.league || '-'),
      mode: formatModeLabel(order.derived && order.derived.mode) || '未知',
      marketType: cleanBetTypeText(order.betType || '') || '盘口',
      createdAt: Number(job.created_at || order.ts || job.ts || 0),
      status: deriveOrderStatus(tasks, itemStatus),
      tasks,
      totalAmount: getFollowTotalAmount(order, job),
      splitCount: tasks.length,
      order,
    });
  }
  return groups;
}

function flattenExecutionTasks(groups) {
  const tasks = [];
  for (const group of groups || []) {
    for (const task of group.tasks || []) tasks.push(task);
  }
  return tasks;
}

function deriveMemberExecutionState(member, tasks, nowSeconds) {
  const status = String((member && member.status) || '').trim().toLowerCase();
  if (status !== 'online') return 'offline';
  const memberId = String((member && member.id) || '').trim();
  if ((tasks || []).some((task) => task && task.memberId === memberId && task.status === 'running')) return 'running';
  const cooldownUntil = Number((member && member.request_cooldown_until) || 0);
  if (cooldownUntil > Number(nowSeconds || Date.now() / 1000)) return 'cooldown';
  return 'idle';
}

function classifyExecutionResult(row, activeOrderIds) {
  const raw = String((row && row.status) || '').trim().toLowerCase();
  if (raw === 'running' || raw === 'executing') return 'running';
  if (row && row.ok === true) return 'success';
  const orderId = String((row && row.orderId) || '').trim();
  if (row && row.ok === false && activeOrderIds && activeOrderIds.has(orderId)) return 'retry';
  return 'failed';
}

function statusLabel(status) {
  return {
    waiting: '待执行',
    running: '执行中',
    success: '成功',
    partial: '部分失败',
    retry: '重试中',
    failed: '失败',
  }[status] || '待执行';
}

function taskStatusLabel(task) {
  const status = String(task && task.status || 'waiting');
  const code = String(task && task.errorCode || '').toUpperCase();
  const message = String(task && task.message || '');
  const detail = `${code} ${message}`;
  if (task && task.ticket) return '提交成功';
  if (/UNKNOWN_RESULT|核验/.test(detail)) return status === 'failed' ? '结果未知' : '结果核验中';
  if (/SNAPSHOT_|TARGET_LINE|MARKET_ERROR|等待.*盘口|盘口变化/.test(detail)) return '等待盘口';
  if (/MEMBER_ERROR|会员失败|切换.*会员/.test(detail)) return '会员失败，切换会员';
  if (status === 'running') return '提交中';
  if (status === 'waiting' && !(task && task.memberId)) return '等待会员';
  return statusLabel(status);
}

function statusIcon(status) {
  return {
    waiting: 'fa-hourglass-start',
    running: 'fa-bolt',
    success: 'fa-check',
    partial: 'fa-triangle-exclamation',
    retry: 'fa-rotate',
    failed: 'fa-xmark',
  }[status] || 'fa-hourglass-start';
}

function orderStatusLabel(status) {
  return status === 'success' ? '全部成功' : statusLabel(status);
}

function memberStateLabel(state) {
  return { idle: '空闲', running: '执行中', cooldown: '冷却中', offline: '掉线' }[state] || '未知';
}

function compactHost(baseUrl) {
  try {
    return new URL(String(baseUrl || '')).host || '-';
  } catch {
    return String(baseUrl || '').replace(/^https?:\/\//i, '') || '-';
  }
}

function timestampTime(value, withMs = false) {
  let n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return '--:--:--';
  if (n < 1e12) n *= 1000;
  const date = new Date(n);
  const pad = (part, len = 2) => String(part).padStart(len, '0');
  const base = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return withMs ? `${base}.${pad(date.getMilliseconds(), 3)}` : base;
}

function attemptSeconds(row) {
  const timings = row && row.timings && typeof row.timings === 'object' ? row.timings : {};
  const attempt = Number(timings.attempt_wall_s);
  return Number.isFinite(attempt) && attempt >= 0 ? attempt : null;
}

function ftBetSeconds(row) {
  const directMs = Number(row && (row.ft_bet_elapsed_ms != null ? row.ft_bet_elapsed_ms : row.submit_elapsed_ms));
  if (Number.isFinite(directMs) && directMs >= 0) return directMs / 1000;
  const timings = row && row.timings && typeof row.timings === 'object' ? row.timings : {};
  const direct = Number(timings.ft_bet_s);
  return Number.isFinite(direct) && direct >= 0 ? direct : null;
}

function orderElapsedSeconds(row) {
  const value = Number(row && row.order_elapsed_s);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function selectedTaskSnapshot(task) {
  if (!task) return null;
  return dashboardSnapshotByGame.get(`${task.mode}|${task.gid}`) || null;
}

function buildMarketSnapshotBadges(simple, derived) {
  if (!simple || typeof simple !== 'object' || !derived) return [];
  const wantWtype = snapshotMarketWtype(derived.wtype);
  const wantLine = derived.target_line_val != null ? Number(derived.target_line_val) : null;
  const wantSide = String(derived.chose_team || '').toUpperCase();
  const out = [];

  if (wantWtype === 'OU') {
    const lines = simple.overUnder && Array.isArray(simple.overUnder.lines) ? simple.overUnder.lines : [];
    const isOver = derived.is_over !== false;
    for (const row of lines) {
      if (!row || snapshotMarketWtype(row.wtype) !== 'OU') continue;
      const pick = isOver ? row.over : row.under;
      if (!pick) continue;
      const line = pick.line || row.rawLine || '-';
      const lineValue = parseSplitLineAvg(line);
      out.push({
        text: `${pick.label || (isOver ? '大' : '小')} ${line} @ ${pick.odd || '-'}`,
        isTarget: wantLine != null && lineValue != null && Math.abs(lineValue - Math.abs(wantLine)) < 1e-6,
      });
    }
    return out;
  }

  if (wantWtype && !wantWtype.endsWith('M')) {
    const lines = simple.handicap && Array.isArray(simple.handicap.lines) ? simple.handicap.lines : [];
    for (const row of lines) {
      if (!row || snapshotMarketWtype(row.wtype) !== wantWtype) continue;
      const pick = wantSide === 'C' ? row.away : row.home;
      if (!pick) continue;
      const line = pick.line || '-';
      const lineValue = parseSignedSplitLineFromText(line);
      out.push({
        text: `${pick.team || ''} ${line} @ ${pick.odd || '-'}`.trim(),
        isTarget: wantLine != null && lineValue != null && Math.abs(lineValue - wantLine) < 1e-6,
      });
    }
  }
  return out;
}

function marketLineBadges(task) {
  const snapshot = selectedTaskSnapshot(task);
  const simple = snapshot && snapshot.simple && typeof snapshot.simple === 'object' ? snapshot.simple : null;
  if (simple && task && task.derived) return buildMarketSnapshotBadges(simple, task.derived);
  const error = snapshotErrorByGame.get(`${task && task.mode ? task.mode : 'live'}|${task && task.gid ? task.gid : ''}`);
  return error ? [{ text: `快照失败：${formatSnapshotError(error)}`, isTarget: false, isError: true }] : [];
}

function snapshotFreshness(task) {
  const row = latestSplitResult(task && task.orderId, task && task.index, task && task.splitCount);
  const source = row && row.snapshot_direct ? row.snapshot_direct : row;
  const age = Number(
    row && (row.snapshot_direct_age_ms != null ? row.snapshot_direct_age_ms : row.snapshot_age_ms != null ? row.snapshot_age_ms : source && source.snapshot_age_ms)
  );
  if (!Number.isFinite(age) || age < 0) return { text: '等待同步', className: 'waiting' };
  if (age < 500) return { text: `${Math.round(age)}ms · 新鲜`, className: 'fresh' };
  if (age <= 1500) return { text: `${(age / 1000).toFixed(2)}s · 注意`, className: 'stale' };
  return { text: `${(age / 1000).toFixed(2)}s · 已过期`, className: 'expired' };
}

function taskExecutionChain(task) {
  const result = latestSplitResult(task && task.orderId, task && task.index, task && task.splitCount);
  const contextMs = Number(result && result.bet_context_elapsed_ms);
  const ftMs = Number(result && (result.ft_bet_elapsed_ms != null ? result.ft_bet_elapsed_ms : result.submit_elapsed_ms));
  const snapshotMs = Number(result && (result.snapshot_direct_age_ms != null ? result.snapshot_direct_age_ms : result.snapshot_age_ms));
  const hasContext = Number.isFinite(contextMs) && contextMs >= 0;
  const hasFtBet = Number.isFinite(ftMs) && ftMs >= 0;
  const ageText = Number.isFinite(snapshotMs) && snapshotMs >= 0 ? `${Math.round(snapshotMs)}ms` : '--';
  const ctxText = hasContext ? `${Math.round(contextMs)}ms` : '--';
  const ftText = hasFtBet ? `${Math.round(ftMs)}ms` : '--';
  const success = task && task.status === 'success';
  const running = task && task.status === 'running';
  const waiting = task && (task.status === 'waiting' || task.status === 'retry');
  return `
    <div class="executionChain" aria-label="执行链">
      <span class="chainNode ${waiting || success || running ? 'done' : ''}"><b>Snapshot</b><small>${ageText}</small></span>
      <i class="fa-solid fa-arrow-right"></i>
      <span class="chainNode ${hasContext || success || running ? 'done' : ''}"><b>Context</b><small>${ctxText}</small></span>
      <i class="fa-solid fa-arrow-right"></i>
      <span class="chainNode ${hasFtBet || success ? 'done' : ''} ${running && !hasFtBet ? 'active' : ''}"><b>FT_bet</b><small>${ftText}</small></span>
      <i class="fa-solid fa-arrow-right"></i>
      <span class="chainNode ${success ? 'success' : ''} ${waiting ? 'pending' : ''}"><b>${success ? 'Success' : waiting ? 'Waiting' : running ? 'Executing' : 'Result'}</b><small>${success && task.ticket ? escapeHtml(task.ticket) : ''}</small></span>
    </div>`;
}

function taskMarketDisplay(task, odd) {
  const derived = task && task.derived ? task.derived : {};
  const rawType = cleanBetTypeText(task && task.order && task.order.betType ? task.order.betType : '');
  const isOu = String(derived.wtype || '').toUpperCase().includes('OU') || /大\s*\/\s*小|大小/.test(rawType);
  const direction = isOu ? (derived.is_over === false ? 'UNDER / 小' : 'OVER / 大') : (derived.chose_team === 'H' ? '主队' : derived.chose_team === 'C' ? '客队' : '独赢');
  const line = derived.target_line_txt || (task && task.order && task.order.line ? String(task.order.line).replace(/^(大|小)\s*/,'') : '-');
  return { type: isOu ? '全场大小球' : (rawType || '比赛盘口'), direction, line, odd: odd || '-' };
}

function splitOrderMatch(match) {
  const raw = String(match || '').trim();
  if (!raw) return { home: '未命名比赛', away: '', separated: false };
  const parts = raw.split(/\s+(?:v(?:s)?\.?|对阵|对)\s+/i).map((item) => item.trim()).filter(Boolean);
  if (parts.length >= 2) return { home: parts[0], away: parts.slice(1).join(' '), separated: true };
  return { home: raw, away: '', separated: false };
}

function orderMarketTags(group) {
  const raw = cleanBetTypeText(group && group.marketType ? group.marketType : '') || '比赛盘口';
  const periodMatch = raw.match(/(?:\s*[-·]\s*)?(上半场|下半场|全场)$/);
  const period = periodMatch ? periodMatch[1] : '';
  const market = periodMatch ? raw.slice(0, periodMatch.index).trim() : raw;
  return {
    market: market || raw,
    period,
    mode: String(group && group.mode || '').trim(),
  };
}

function orderCardMarket(group) {
  const tasks = Array.isArray(group && group.tasks) ? group.tasks : [];
  const task = tasks.find((item) => item && item.status === 'success') || tasks[0] || null;
  if (!task) return { direction: '-', odd: '-' };
  const snapshot = selectedTaskSnapshot(task);
  const odd = extractCurrentOddFromSnapshot(snapshot && snapshot.simple, task.derived) || task.sourceOdd || '-';
  const market = taskMarketDisplay(task, odd);
  const direction = String(market.direction || '')
    .split('/')
    .map((item) => item.trim())
    .filter(Boolean)
    .pop() || '-';
  const line = String(market.line || '').trim();
  return {
    direction: [direction, line && line !== '-' ? line : ''].filter(Boolean).join(' '),
    odd: market.odd || '-',
  };
}

function shouldRequestTaskSnapshot(status, state, snapshotCtx, now = Date.now(), refreshMs = 2200) {
  const normalized = String(status || '').toLowerCase();
  if (!['job', 'pending', 'failed'].includes(normalized)) return false;
  const previousCtx = state && String(state.lastFollowSnapshotCtx || '');
  if (previousCtx !== String(snapshotCtx || '')) return true;
  // Terminal failures are historical records: fetch once for diagnosis, then stop polling them.
  if (normalized === 'failed') return false;
  const lastAt = Number(state && state.lastFollowOddsAt || 0);
  return !lastAt || now - lastAt >= refreshMs;
}

function memberExecutionStats(memberId) {
  const rows = (dashboardResults || []).filter((row) => String(row.member_id || '') === String(memberId || ''));
  const success = rows.filter((row) => row.ok === true).length;
  const attempts = rows.map(attemptSeconds).filter((value) => value != null);
  const ftBets = rows.map(ftBetSeconds).filter((value) => value != null);
  const orderElapsed = rows.map(orderElapsedSeconds).filter((value) => value != null);
  const lastExecutionAt = rows.reduce((latest, row) => {
    const value = Number(row && (row.ts_ms != null ? row.ts_ms : row.ts));
    return Number.isFinite(value) && value > latest ? value : latest;
  }, 0);
  return {
    total: rows.length,
    successRate: rows.length ? (success / rows.length) * 100 : 0,
    avgAttemptSeconds: attempts.length ? attempts.reduce((sum, value) => sum + value, 0) / attempts.length : null,
    avgFtBetSeconds: ftBets.length ? ftBets.reduce((sum, value) => sum + value, 0) / ftBets.length : null,
    avgOrderElapsedSeconds: orderElapsed.length ? orderElapsed.reduce((sum, value) => sum + value, 0) / orderElapsed.length : null,
    lastExecutionAt,
  };
}

function renderMemberPool() {
  const box = $('memberPool');
  if (!box) return;
  const tasks = flattenExecutionTasks(dashboardTaskGroups);
  const now = Date.now() / 1000;
  const stateCounts = { idle: 0, running: 0, cooldown: 0, offline: 0 };
  let onlineCount = 0;
  const cards = [];

  const executionMembers = (lastMembersSnapshot || []).filter(isBetExecutionMember);
  for (const member of executionMembers) {
    const memberId = String(member.id || '');
    const state = deriveMemberExecutionState(member, tasks, now);
    stateCounts[state] = (stateCounts[state] || 0) + 1;
    if (String(member.status || '').toLowerCase() === 'online') onlineCount += 1;
    const stats = memberExecutionStats(memberId);
    const activeTask = tasks.find((task) => task.memberId === memberId && task.status === 'running');
    const cooldownSeconds = Math.max(0, Math.ceil(Number(member.request_cooldown_until || 0) - now));
    const ftBetText = stats.avgFtBetSeconds == null ? '-' : `${stats.avgFtBetSeconds.toFixed(2)}s`;
    const footText = state === 'running'
      ? `FT_bet ${ftBetText} · 正在执行 ${activeTask ? activeTask.label : '拆分单'}`
      : state === 'cooldown'
        ? `FT_bet ${ftBetText} · 冷却剩余 ${formatCountdown(now + cooldownSeconds)}`
        : state === 'offline'
          ? `FT_bet ${ftBetText} · 最后在线 ${timestampTime(member.last_ok_at)}`
          : `FT_bet ${ftBetText} · 等待任务分配`;
    const label = String(member.label || member.username || member.id || '未命名会员');
    cards.push(`
      <article class="memberCard ${state}" data-member-id="${escapeHtml(memberId)}">
        <div class="memberHead">
          <div class="memberIdentity"><span class="memberAvatar"><i class="fa-solid fa-user"></i></span><div><strong>${escapeHtml(label)}</strong><small>${escapeHtml(compactHost(member.base_url))}</small></div></div>
          <span class="memberState ${state}"><i class="fa-solid ${state === 'cooldown' ? 'fa-diamond' : 'fa-circle'}"></i>${memberStateLabel(state)}</span>
        </div>
        <div class="memberMode"><span>执行通道</span><strong>FT_bet 自动跟单</strong><span class="memberDetailHint">在线</span></div>
        <div class="memberStats">
          <div><span>可用额度</span><strong>¥${escapeHtml(formatAmount(member.balance || member.maxcredit || 0))}</strong></div>
          <div><span>最后执行</span><strong>${stats.lastExecutionAt ? timestampTime(stats.lastExecutionAt) : '-'}</strong></div>
          <div><span>平均延迟</span><strong>${stats.avgAttemptSeconds == null ? '-' : `${Math.round(stats.avgAttemptSeconds * 1000)}ms`}</strong></div>
          <div><span>FT_bet 心跳</span><strong>${stats.avgFtBetSeconds == null ? '-' : `${Math.round(stats.avgFtBetSeconds * 1000)}ms`}</strong></div>
        </div>
        <div class="memberFoot ${state}" title="${escapeHtml(String(member.last_error || footText))}"><span>${escapeHtml(footText)}</span><b>${stats.total ? `${stats.successRate.toFixed(1)}% 成功率` : '暂无执行记录'}</b></div>
      </article>`);
  }
  const nextHtml = cards.join('') || '<div class="taskEmpty" style="display:grid;grid-column:1/-1;min-height:120px"><strong>暂无会员执行通道</strong><span>请先在会员池添加并登录账号</span></div>';
  updateHtmlIfChanged(box, nextHtml, box);

  setText('stOnlineMembers', onlineCount);
  setText('stTotalMembers', executionMembers.length);
  setText('kpiOnline', onlineCount);
  setText('kpiIdle', stateCounts.idle || 0);
  setText('kpiRunning', stateCounts.running || 0);
  setText('kpiOffline', stateCounts.offline || 0);
}

function visibleTaskGroups() {
  const onlyActive = Boolean($('ckOnlyActive') && $('ckOnlyActive').checked);
  const query = String(($('inSearch') && $('inSearch').value) || '').trim().toLowerCase();
  const groups = [];
  for (const group of dashboardTaskGroups || []) {
    const haystack = [group.orderId, group.match, group.league, group.marketType, group.order && group.order.line]
      .map((value) => String(value || '').toLowerCase())
      .join(' ');
    if (query && !haystack.includes(query)) continue;
    let tasks = (group.tasks || []).filter((task) => {
      if (onlyActive && ['success', 'failed'].includes(task.status)) return false;
      return activeTaskFilter === 'all' || task.status === activeTaskFilter;
    });
    if (!tasks.length) continue;
    groups.push({ ...group, tasks });
  }
  return groups;
}

function renderTaskTabs(tasks) {
  const counts = { all: tasks.length, waiting: 0, running: 0, success: 0, retry: 0, failed: 0 };
  for (const task of tasks) counts[task.status] = (counts[task.status] || 0) + 1;
  setText('tabAllCount', counts.all);
  setText('tabWaitingCount', counts.waiting);
  setText('tabRunningCount', counts.running);
  setText('tabSuccessCount', counts.success);
  setText('tabRetryCount', counts.retry);
  setText('tabFailedCount', counts.failed);
  for (const button of document.querySelectorAll('.taskTab')) {
    const selected = String(button.dataset.taskFilter || '') === activeTaskFilter;
    button.classList.toggle('active', selected);
    const ariaSelected = selected ? 'true' : 'false';
    if (button.getAttribute('aria-selected') !== ariaSelected) {
      button.setAttribute('aria-selected', ariaSelected);
    }
  }
  return counts;
}

function renderOrderPool() {
  const box = $('boxQueue');
  const empty = $('boxEmpty');
  if (!box || !empty) return;
  const allTasks = flattenExecutionTasks(dashboardTaskGroups);
  const counts = renderTaskTabs(allTasks);
  const groups = visibleTaskGroups();
  setText('stOrderTotal', `${groups.length} 个订单`);
  setText('stPendingCount', counts.waiting + counts.retry);
  setText('stJobsCount', counts.running);
  setText('kpiWaitingTasks', counts.waiting + counts.retry);
  setText('kpiRunningTasks', counts.running);

  if (!groups.length) {
    updateHtmlIfChanged(box, '', $('orderPool') || box);
    empty.style.display = 'grid';
    return;
  }
  empty.style.display = 'none';
  const html = [];
  for (const group of groups) {
    const groupStatus = group.status || deriveOrderStatus(group.tasks, '');
    const totalText = group.totalAmount == null ? '-' : `¥${formatAmount(group.totalAmount)}`;
    const orderDate = formatOrderDateTime(group.createdAt);
    const taskCount = Math.max(1, toCount(group.splitCount) || group.tasks.length || 1);
    const teams = splitOrderMatch(group.match);
    const tags = orderMarketTags(group);
    const cardMarket = orderCardMarket(group);
    const matchMarkup = teams.separated
      ? `<div class="orderTeam homeTeam"><span>主队</span><strong title="${escapeHtml(teams.home)}">${escapeHtml(teams.home)}</strong></div><span class="orderVersus" aria-hidden="true">VS</span><div class="orderTeam awayTeam"><span>客队</span><strong title="${escapeHtml(teams.away)}">${escapeHtml(teams.away)}</strong></div>`
      : `<div class="orderTeam singleTeam"><span>比赛</span><strong title="${escapeHtml(teams.home)}">${escapeHtml(teams.home)}</strong></div>`;
    html.push(`
      <section class="orderGroup orderSummaryCard ${groupStatus}" data-order-key="${escapeHtml(group.key)}" aria-label="订单 #${escapeHtml(group.orderId)}">
        <header class="orderCardTop">
          <div class="orderCardTitle"><span>订单</span><strong>#${escapeHtml(group.orderId)}</strong><i aria-hidden="true"></i><b>${taskCount} 单</b></div>
          <span class="orderStatusBadge ${groupStatus}"><i class="fa-solid ${statusIcon(groupStatus)}" aria-hidden="true"></i>${orderStatusLabel(groupStatus)}</span>
        </header>
        <div class="orderTagRow" aria-label="订单玩法">
          <span class="orderTag marketTag">${escapeHtml(tags.market)}</span>
          ${tags.period ? `<span class="orderTag periodTag">${escapeHtml(tags.period)}</span>` : ''}
          ${tags.mode ? `<span class="orderTag modeTag">${escapeHtml(tags.mode)}</span>` : ''}
        </div>
        <div class="orderMatchPanel ${teams.separated ? '' : 'singleMatch'}" aria-label="${escapeHtml(group.match)}">${matchMarkup}</div>
        <div class="orderMetricGrid" aria-label="订单摘要">
          <div><span>投注单数</span><strong class="countMetric">${taskCount} 单</strong></div>
          <div><span>投注金额</span><strong class="amountMetric">${escapeHtml(totalText)}</strong></div>
          <div><span>投注方向</span><strong title="${escapeHtml(cardMarket.direction)}">${escapeHtml(cardMarket.direction)}</strong></div>
          <div><span>水位</span><strong class="oddsMetric">${escapeHtml(cardMarket.odd)}</strong></div>
          <div><span>状态</span><strong class="statusMetric ${groupStatus}"><i class="fa-solid ${statusIcon(groupStatus)}" aria-hidden="true"></i>${orderStatusLabel(groupStatus)}</strong></div>
        </div>
        <footer class="orderCardFooter">
          <div><i class="fa-regular fa-clock" aria-hidden="true"></i><span>下单时间</span><time datetime="${escapeHtml(orderDate.replace(' ', 'T'))}">${escapeHtml(orderDate)}</time></div>
          <a class="orderDetailLink" href="/chashui/index.html" target="_blank" rel="noopener" aria-label="查看订单 #${escapeHtml(group.orderId)} 的盘口详情">查看详情<i class="fa-solid fa-chevron-right" aria-hidden="true"></i></a>
        </footer>
      </section>`);
  }
  updateHtmlIfChanged(box, html.join(''), $('orderPool') || box);
}

function resultFailureCategory(row) {
  const message = String((row && (row.message || row.error_type)) || '').toLowerCase();
  if (/水位|赔率|odd|beterror/.test(message)) return '水位变动';
  if (/余额|额度|金额|上限|下限/.test(message)) return '额度限制';
  if (/盘口|封盘|line|game/.test(message)) return '盘口不可用';
  if (/timeout|超时|network|connection|request/.test(message)) return '网络超时';
  return '其他失败';
}

function renderFailurePanel() {
  const failed = (dashboardResults || []).filter((row) => row.ok === false);
  const categories = ['水位变动', '额度限制', '盘口不可用', '网络超时', '其他失败'];
  const colors = ['#ff6077', '#f6aa2b', '#9570ff', '#4388ff', '#25c8ff'];
  const counts = Object.fromEntries(categories.map((name) => [name, 0]));
  for (const row of failed) counts[resultFailureCategory(row)] += 1;
  setText('failureTotal', failed.length);
  const legend = $('failureLegend');
  if (legend) {
    updateHtmlIfChanged(legend, categories.map((name, index) => `<div><i style="background:${colors[index]}"></i><span>${name}</span><strong>${counts[name]}</strong></div>`).join(''), legend);
  }
  const donut = $('failureDonut');
  if (donut && failed.length) {
    let cursor = 0;
    const segments = [];
    categories.forEach((name, index) => {
      const start = cursor;
      cursor += (counts[name] / failed.length) * 100;
      segments.push(`${colors[index]} ${start.toFixed(1)}% ${cursor.toFixed(1)}%`);
    });
    const background = `conic-gradient(${segments.join(',')})`;
    if (donut.style.background !== background) donut.style.background = background;
  }
}

function renderExecutionLog() {
  const box = $('executionLog');
  if (!box) return;
  const activeOrderIds = new Set(dashboardTaskGroups.filter((group) => !['done', 'failed'].includes(group.status)).map((group) => group.orderId));
  const onlyErrors = Boolean($('ckOnlyErrors') && $('ckOnlyErrors').checked);
  const splitFilter = String(activeLogSplit || 'all');
  const stageForRow = (row) => {
    const raw = `${String(row && row.status || '')} ${String(row && row.execution_state || '')} ${String(row && row.error_class || '')} ${String(row && row.message || '')}`.toLowerCase();
    if ((row && row.ok === true) || (/success|ft_bet/.test(raw) && row && row.ticket_id)) return 'ft_bet';
    if (/snapshot|waiting_market|等待.*盘口|盘口快照/.test(raw)) return 'waiting';
    if (/context|bet_context|market_error|盘口变化/.test(raw)) return 'context';
    return row && row.ok === false ? 'error' : 'ft_bet';
  };
  const rows = (dashboardResults || [])
    .map((row) => ({ row, status: classifyExecutionResult(row, activeOrderIds), stage: stageForRow(row) }))
    .filter((item) => !onlyErrors || ['retry', 'failed'].includes(item.status))
    .filter((item) => activeLogFilter === 'all' || (activeLogFilter === 'success' ? item.status === 'success' : ['failed', 'error'].includes(activeLogFilter) ? item.status === 'failed' : activeLogFilter === 'retry' ? item.status === 'retry' : item.stage === activeLogFilter))
    .filter((item) => splitFilter === 'all' || String(Number(item.row.split_index || 0)) === splitFilter)
    .slice(0, 24);
  if (!rows.length) {
    updateHtmlIfChanged(box, '<div class="taskEmpty" style="display:grid;min-height:88px;grid-column:1/-1"><strong>暂无执行日志</strong><span>拆分单开始执行后会实时写入</span></div>', box);
    return;
  }
  const icon = { success: 'fa-check', running: 'fa-bolt', retry: 'fa-rotate', failed: 'fa-xmark' };
  const nextHtml = rows.map(({ row, status, stage }) => {
    const taskNo = Number(row.split_index || 0);
    const label = taskNo > 0 ? getSplitOrderName(taskNo - 1) : `订单 #${String(row.orderId || '-').slice(-8)}`;
    const member = String(row.member || row.member_id || '未分配');
    const message = row.ok === true
      ? `会员 ${member} 提交成功${row.ticket_id ? ` · ticket ${row.ticket_id}` : ''}`
      : `会员 ${member} ${displayExecutionMessage(row.message || row.error_type) || '执行失败'}`;
    const stageLabel = stage === 'waiting' ? 'Snapshot' : stage === 'context' ? 'BetContext' : 'FT_bet';
    const stateLabel = row.ok === true ? `成功 · ${row.ticket_id ? `ticket ${row.ticket_id}` : '已提交'}` : displayExecutionMessage(row.message || row.error_type) || (status === 'retry' ? '等待重试' : '执行失败');
    const elapsed = orderElapsedSeconds(row);
    const orderId = String(row.orderId || row.order_id || '-');
    const memberLabel = String(row.member || row.member_id || '未分配');
    return `<div class="logRow ${status}" title="${escapeHtml(`${label} · ${message}`)}"><time>${timestampTime(row.ts_ms || row.ts, true)}</time><strong class="logOrder">#${escapeHtml(orderId)}</strong><span class="logTask">${escapeHtml(label)} <em>${escapeHtml(stageLabel)}</em></span><span class="logMember">${escapeHtml(memberLabel)}</span><span class="logState ${status}"><i class="fa-solid ${icon[status] || 'fa-circle'}"></i>${escapeHtml(stateLabel)}</span><span class="logDetail">${escapeHtml(displayExecutionMessage(row.message || row.error_type) || (row.ticket_id ? `ticket ${row.ticket_id}` : '已提交'))}</span><time class="logElapsed">${elapsed == null ? '-' : `${elapsed.toFixed(2)}s`}</time></div>`;
  }).join('');
  updateHtmlIfChanged(box, nextHtml, box);
}

function snapshotVersion() {
  const seed = String(lastQueueMeta.updatedAt || Date.now()).replace(/\D/g, '').slice(-6);
  return `S${String(seed || snapshotRevision).padStart(6, '0')}`;
}

function renderPerformanceAndSnapshot() {
  const tasks = flattenExecutionTasks(dashboardTaskGroups);
  const waiting = tasks.filter((task) => task.status === 'waiting' || task.status === 'retry').length;
  const running = tasks.filter((task) => task.status === 'running').length;
  const executionMembers = (lastMembersSnapshot || []).filter(isBetExecutionMember);
  const online = executionMembers.filter((member) => String(member.status || '').toLowerCase() === 'online').length;
  const success = toCount(dashboardCounts.done_ok);
  const failed = toCount(dashboardCounts.done_failed);
  const totalTerminal = success + failed;
  const successRate = totalTerminal ? (success / totalTerminal) * 100 : 0;
  const attemptTimings = (dashboardResults || []).map(attemptSeconds).filter((value) => value != null);
  const ftBetTimings = (dashboardResults || []).map(ftBetSeconds).filter((value) => value != null);
  const orderTimings = (dashboardResults || []).map(orderElapsedSeconds).filter((value) => value != null);
  const avgAttempt = attemptTimings.length ? attemptTimings.reduce((sum, value) => sum + value, 0) / attemptTimings.length : null;
  const avgFtBet = ftBetTimings.length ? ftBetTimings.reduce((sum, value) => sum + value, 0) / ftBetTimings.length : null;
  const avgOrder = orderTimings.length ? orderTimings.reduce((sum, value) => sum + value, 0) / orderTimings.length : null;
  const load = online ? Math.min(100, (running / online) * 100) : 0;
  const health = executionMembers.length ? (online / executionMembers.length) * 100 : 0;
  const version = snapshotVersion();
  const games = new Set(tasks.map((task) => task.gid).filter(Boolean));

  setText('kpiSuccessRate', `${successRate.toFixed(1)}%`);
  setText('kpiAvgAttempt', avgAttempt == null ? '-' : `${avgAttempt.toFixed(2)}s`);
  setText('kpiAvgFtBet', avgFtBet == null ? '-' : `${avgFtBet.toFixed(2)}s`);
  setText('kpiAvgOrderElapsed', avgOrder == null ? '-' : `${avgOrder.toFixed(2)}s`);
  setText('kpiSnapshotVersion', version);
  setText('kpiHealth', `${health.toFixed(1)}%`);
  setText('kpiHealthState', health >= 90 ? 'Healthy' : health >= 60 ? 'Degraded' : 'Attention');
  const healthState = $('kpiHealthState');
  if (healthState) healthState.dataset.state = health >= 90 ? 'healthy' : health >= 60 ? 'degraded' : 'attention';
  setText('stSystemLoad', `${load.toFixed(0)}%`);
  setText('stHistoryTasks', toCount(dashboardCounts.done));
  setText('stSuccessCount', success);
  setText('stFailedCount', failed);
  setText('stPerformanceTime', fmtNowTime());
  setText('stStatsUpdated', lastQueueMeta.updatedAt ? String(lastQueueMeta.updatedAt).split(' ').pop() : '实时');
  setText('stCurrentTime', fmtNowTime());
  const dateEl = $('stCurrentDate');
  if (dateEl) {
    const d = new Date();
    const weekday = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
    dateEl.textContent = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 周${weekday}`;
  }
  const strategyName = $('stStrategyName');
  const strategyDesc = $('stStrategyDesc');
  if (strategyName && followSettingsCache) strategyName.textContent = `FT_bet 自动分单 · ${followSettingsCache.split_count} 路`;
  if (strategyDesc && followSettingsCache) strategyDesc.textContent = `分单延迟 ${followSettingsCache.split_delay_seconds}s · 探测窗口 ${followSettingsCache.wait_line_not_found_max_seconds}s`;
  setText('kpiWaitingTasks', waiting);
  setText('kpiRunningTasks', running);
}

function renderExecutionCenter() {
  dashboardTaskGroups = buildExecutionTaskModels(dashboardQueueItems);
  renderMemberPool();
  renderOrderPool();
  renderFailurePanel();
  renderExecutionLog();
  renderPerformanceAndSnapshot();
}

async function refreshExecutionResults() {
  if (executionResultsRefreshInFlight) return executionResultsRefreshInFlight;
  const request = (async () => {
    try {
      const data = await fetchJson('/api/follow-results?limit=1000');
      dashboardResults = Array.isArray(data.results) ? data.results : [];
      renderExecutionCenter();
    } catch (error) {
      setErrBox(error.message || String(error));
    }
  })();
  executionResultsRefreshInFlight = request;
  try {
    return await request;
  } finally {
    if (executionResultsRefreshInFlight === request) {
      executionResultsRefreshInFlight = null;
    }
  }
}

function refreshActiveSnapshots() {
  const memberId = getSelectedMemberId();
  if (!memberId) return;
  const seen = new Set();
  const activeTasks = flattenExecutionTasks(dashboardTaskGroups).filter((task) => ['waiting', 'running', 'retry'].includes(task.status));
  for (const task of activeTasks) {
    if (!task.gid) continue;
    const key = `${task.mode}|${task.gid}`;
    if (seen.has(key)) continue;
    seen.add(key);
    requestFollowSnapshot({ memberId, gid: task.gid, mode: task.mode })
      .then((data) => {
        dashboardSnapshotByGame.set(key, data || null);
        snapshotErrorByGame.delete(key);
        snapshotRevision += 1;
        renderOrderPool();
        renderPerformanceAndSnapshot();
      })
      .catch((error) => {
        snapshotErrorByGame.set(key, error && error.message ? error.message : String(error || 'snapshot_failed'));
        renderOrderPool();
      });
  }
}

function createOrderCardState(cardKey) {
  const card = document.createElement('div');
  card.className = 'orderCard';
  card.dataset.key = cardKey;

  const head = document.createElement('div');
  head.className = 'orderHead';

  const left = document.createElement('div');

  const titleRow = document.createElement('div');
  titleRow.className = 'orderTitleRow';

  const badge = document.createElement('span');
  badge.className = 'badge pending';
  badge.textContent = '待入队';
  titleRow.appendChild(badge);

  const title = document.createElement('div');
  title.className = 'orderTitle';
  title.textContent = '未命名比赛';
  titleRow.appendChild(title);

  left.appendChild(titleRow);

  const sub = document.createElement('div');
  sub.className = 'orderSub';
  sub.textContent = '-';
  left.appendChild(sub);

  const right = document.createElement('div');
  right.className = 'orderHeadRight';

  const berr = document.createElement('span');
  berr.className = 'badge err mono';
  berr.style.display = 'none';
  right.appendChild(berr);

  const btn = document.createElement('a');
  btn.className = 'btn btn-ghost btn-small';
  btn.textContent = '单场查水';
  btn.href = '/chashui/index.html';
  btn.target = '_blank';
  right.appendChild(btn);

  head.appendChild(left);
  head.appendChild(right);

  const body = document.createElement('div');
  body.className = 'orderBody';

  const meta = document.createElement('div');
  meta.className = 'metaGrid';

  const metaStage = makeMetaItem('处理阶段', {});
  const metaStake = makeMetaItem('金额', { mono: true });
  const metaBackendOdd = makeMetaItem('后台水位（原单）', { mono: true });

  const metaSplit = makeMetaItem('分单进度', { mono: true });
  const metaAttempts = makeMetaItem('尝试次数', { mono: true });
  const metaNext = makeMetaItem('下次尝试', { mono: true });
  const metaFollowTotal = makeMetaItem('跟单总额', { mono: true });
  const metaFollowedAmount = makeMetaItem('已跟金额', { mono: true });
  const metaAvailableMembers = makeMetaItem('可用会员数', { mono: true });
  const metaHint = makeMetaItem('提示', { span: 4 });
  const metaDerivedErr = makeMetaItem('解析异常', { mono: true, span: 4 });

  for (const x of [
    metaStage,
    metaStake,
    metaBackendOdd,
    metaSplit,
    metaAttempts,
    metaNext,
    metaFollowTotal,
    metaFollowedAmount,
    metaAvailableMembers,
    metaHint,
    metaDerivedErr,
  ]) {
    meta.appendChild(x.item);
  }

  metaSplit.item.style.display = 'none';
  metaAttempts.item.style.display = 'none';
  metaNext.item.style.display = 'none';
  metaHint.item.style.display = 'none';
  metaDerivedErr.item.style.display = 'none';

  const metaDetails = document.createElement('details');
  metaDetails.className = 'metaDetails';
  const metaSummary = document.createElement('summary');
  metaSummary.textContent = '订单详情';
  metaDetails.appendChild(metaSummary);

  const detKv = document.createElement('div');
  detKv.className = 'metaKv';
  const detTarget = makeKvRow('目标账号', true);
  const detOrderId = makeKvRow('后台单号', true);
  const detGid = makeKvRow('比赛编号', true);
  const detDerivedErr = makeKvRow('解析异常', true);
  const detErrorType = makeKvRow('异常类型', true);
  const detErrorMsg = makeKvRow('最近结果', false);
  const detException = makeKvRow('异常明细', true);
  const detResponseHead = makeKvRow('响应摘要', true);
  for (const x of [detTarget, detOrderId, detGid, detDerivedErr, detErrorType, detErrorMsg, detException, detResponseHead]) detKv.appendChild(x.row);
  metaDetails.appendChild(detKv);
  meta.appendChild(metaDetails);

  const markets = document.createElement('div');
  markets.className = 'marketWrap';

  const friendlyError = document.createElement('div');
  friendlyError.className = 'friendlyErrorWrap';
  friendlyError.style.display = 'none';

  const followCard = document.createElement('div');
  followCard.className = 'simpleCard compact followMarketCard';
  const followTitle = document.createElement('div');
  followTitle.className = 'simpleTitle';
  followTitle.textContent = '跟单盘口';
  followCard.appendChild(followTitle);
  const followBox = document.createElement('div');
  followBox.className = 'followMarketValue mono';
  followBox.textContent = '-';
  followCard.appendChild(followBox);
  markets.appendChild(followCard);

  body.appendChild(meta);
  body.appendChild(friendlyError);
  body.appendChild(markets);

  card.appendChild(head);
  card.appendChild(body);

  return {
    card,
    refs: {
      badge,
      title,
      sub,
      berr,
      metaStage: metaStage.valueEl,
      metaStake: metaStake.valueEl,
      metaBackendOdd: metaBackendOdd.valueEl,
      metaSplitItem: metaSplit.item,
      metaSplit: metaSplit.valueEl,
      metaAttemptsItem: metaAttempts.item,
      metaAttempts: metaAttempts.valueEl,
      metaNextItem: metaNext.item,
      metaNext: metaNext.valueEl,
      metaFollowTotal: metaFollowTotal.valueEl,
      metaFollowedAmount: metaFollowedAmount.valueEl,
      metaAvailableMembers: metaAvailableMembers.valueEl,
      metaHintItem: metaHint.item,
      metaHint: metaHint.valueEl,
      friendlyError,
      metaDerivedErrItem: metaDerivedErr.item,
      metaDerivedErr: metaDerivedErr.valueEl,
      detTarget: detTarget.valueEl,
      detOrderId: detOrderId.valueEl,
      detGid: detGid.valueEl,
      detDerivedErr: detDerivedErr.valueEl,
      detErrorType: detErrorType.valueEl,
      detErrorMsg: detErrorMsg.valueEl,
      detException: detException.valueEl,
      detResponseHead: detResponseHead.valueEl,
      followMarket: followBox,
    },
    lastOddsSig: '',
    lastOddsAt: 0,
    lastSnapshotCtx: '',
    lastFollowSnapshot: null,
    lastFollowSnapshotCtx: '',
    lastFollowOddsAt: 0,
  };
}

function updateCardFromItem(st, it) {
  const status = String(it.status || '');
  const order = it.order || {};
  const job = it.job || null;
  const derived = order.derived || {};
  const lastResult = order.last_result || null;
  const terminalReason = job && (job.reason || job.last_message) ? displayExecutionMessage(job.reason || job.last_message) : '';
  const terminalTicket = job && job.ticket_id ? String(job.ticket_id) : '';

  const stageText = formatQueueStage(status);
  st.refs.badge.className = `badge ${status}`;
  st.refs.badge.textContent = stageText;

  st.refs.title.textContent = formatMatchTitle(order);
  st.refs.sub.textContent = buildSubLine(order, derived);
  renderFollowMarketSnapshot(st.refs.followMarket, order, derived, st.lastFollowSnapshot, it.key || '');

  const errCode = extractErrorCode(job);
  if (job && errCode) {
    st.refs.berr.style.display = '';
    st.refs.berr.textContent = String(errCode || '');
  } else {
    st.refs.berr.style.display = 'none';
    st.refs.berr.textContent = '';
  }

  st.refs.metaStage.textContent = stageText || '-';
  st.refs.metaStake.textContent = order.stake == null || order.stake === '' ? '-' : String(order.stake);
  st.refs.metaBackendOdd.textContent = order.odds == null || order.odds === '' ? '-' : String(order.odds);
  const followTotal = getFollowTotalAmount(order, job);
  const followedAmount = getFollowedAmount(job, followTotal);
  st.refs.metaFollowTotal.textContent = followTotal == null ? '-' : formatAmount(followTotal);
  st.refs.metaFollowedAmount.textContent = formatAmount(followedAmount);
  st.refs.metaAvailableMembers.textContent = `${getAvailableBetMemberCount()} 个`;

  st.refs.detTarget.textContent = order.target == null || order.target === '' ? '-' : String(order.target);
  st.refs.detOrderId.textContent = order.orderId == null || order.orderId === '' ? '-' : String(order.orderId);
  st.refs.detGid.textContent = order.gid == null || order.gid === '' ? '-' : String(order.gid);
  st.refs.detErrorType.textContent = lastResult && lastResult.error_type ? String(lastResult.error_type) : '-';
  st.refs.detErrorMsg.textContent = lastResult && lastResult.message ? displayExecutionMessage(lastResult.message) : '-';
  st.refs.detException.textContent = lastResult && lastResult.exception ? String(lastResult.exception) : '-';
  st.refs.detResponseHead.textContent = lastResult && lastResult.response_head ? String(lastResult.response_head) : '-';

  if (job && !job.terminal) {
    st.refs.metaSplitItem.style.display = '';
    st.refs.metaAttemptsItem.style.display = '';
    st.refs.metaNextItem.style.display = '';
    st.refs.metaSplit.textContent = `${Number(job.split_index || 0) + 1}/${job.split_count || 1}`;
    st.refs.metaAttempts.textContent = job.attempts == null ? '-' : String(job.attempts);
    st.refs.metaNext.textContent = job.next_try_at ? formatCountdown(job.next_try_at) : '-';
    if (job.last_message && !errCode) {
      st.refs.metaHintItem.style.display = '';
      st.refs.metaHint.textContent = displayExecutionMessage(job.last_message);
    } else {
      st.refs.metaHintItem.style.display = 'none';
      st.refs.metaHint.textContent = '';
    }
  } else if (status === 'done' || status === 'failed' || status === 'error') {
    st.refs.metaSplitItem.style.display = 'none';
    st.refs.metaAttemptsItem.style.display = 'none';
    st.refs.metaNextItem.style.display = 'none';
    st.refs.metaHintItem.style.display = '';
    if (status === 'done') {
      st.refs.metaHint.textContent = terminalTicket ? `订单已完成，票据：${terminalTicket}` : '订单已完成';
    } else {
      st.refs.metaHint.textContent = terminalReason ? `失败原因：${terminalReason}` : '订单失败';
    }
  } else {
    st.refs.metaSplitItem.style.display = 'none';
    st.refs.metaAttemptsItem.style.display = 'none';
    st.refs.metaNextItem.style.display = 'none';
    st.refs.metaHintItem.style.display = 'none';
    st.refs.metaHint.textContent = '';
  }

  const errDetails = terminalReason || (job && job.last_message ? displayExecutionMessage(job.last_message) : '');
  if (job && !job.terminal && errCode) {
    st.refs.friendlyError.style.display = '';
    st.refs.friendlyError.innerHTML = formatError(errCode, errDetails);
  } else {
    st.refs.friendlyError.style.display = 'none';
    st.refs.friendlyError.innerHTML = '';
  }

  if (order.derived_error) {
    st.refs.metaDerivedErrItem.style.display = '';
    st.refs.metaDerivedErr.textContent = '存在异常（展开“订单详情”查看）';
    st.refs.detDerivedErr.textContent = String(order.derived_error || '');
  } else {
    st.refs.metaDerivedErrItem.style.display = 'none';
    st.refs.metaDerivedErr.textContent = '';
    st.refs.detDerivedErr.textContent = '-';
  }
}

function extractCurrentOddFromSnapshot(simple, derived) {
  if (!simple || !derived) return null;
  const wantWtype = baseWtype(derived.wtype);
  if (!wantWtype) return null;

  const wantSide = String(derived.chose_team || '').toUpperCase();

  // 独赢
  if (wantWtype.endsWith('M')) {
    const ml = simple.moneyLine;
    if (!ml || ml.empty) return null;
    if (wantSide === 'H') return ml.home && ml.home.odd ? String(ml.home.odd) : null;
    if (wantSide === 'C') return ml.away && ml.away.odd ? String(ml.away.odd) : null;
    if (wantSide === 'N') return ml.draw && ml.draw.odd ? String(ml.draw.odd) : null;
    return null;
  }

  const wantLine = derived.target_line_val != null ? Number(derived.target_line_val) : null;
  if (wantLine == null || !Number.isFinite(wantLine)) return null;

  // 大小盘
  if (wantWtype.endsWith('OU')) {
    const isOver = derived.is_over != null ? Boolean(derived.is_over) : null;
    if (isOver == null) return null;
    const lines = simple.overUnder && simple.overUnder.lines ? simple.overUnder.lines : [];
    for (const row of lines || []) {
      const rowWtype = baseWtype(row.wtype);
      if (!rowWtype || rowWtype !== wantWtype) continue;
      const baseLine = row.rawLine || (row.over && row.over.line) || (row.under && row.under.line) || '';
      const rowVal = parseSplitLineAvg(baseLine);
      if (rowVal == null) continue;
      if (Math.abs(rowVal - Math.abs(wantLine)) >= 1e-6) continue;
      const pick = isOver ? row.over : row.under;
      const odd = pick && pick.odd ? String(pick.odd) : '';
      if (odd) return odd;
    }
    return null;
  }

  // 让球盘
  if (wantSide !== 'H' && wantSide !== 'C') return null;
  const lines = simple.handicap && simple.handicap.lines ? simple.handicap.lines : [];
  for (const row of lines || []) {
    const rowWtype = baseWtype(row.wtype);
    if (!rowWtype || rowWtype !== wantWtype) continue;
    const pick = wantSide === 'H' ? row.home : row.away;
    if (!pick) continue;
    const lineVal = parseSignedSplitLineFromText(pick.line);
    if (lineVal == null) continue;
    if (Math.abs(lineVal - wantLine) >= 1e-6) continue;
    const odd = pick.odd ? String(pick.odd) : '';
    if (odd) return odd;
  }
  return null;
}

function renderQueue(items) {
  const box = $('boxQueue');
  const empty = $('boxEmpty');
  if (!box || !empty) return;

  const onlyActive = $('ckOnlyActive') && $('ckOnlyActive').checked;
  const q = $('inSearch') ? $('inSearch').value : '';

  const filtered = [];
  for (const it of items || []) {
    if (onlyActive && ['done', 'failed', 'error', 'skipped'].includes(String(it.status || '').toLowerCase())) continue;
    if (!shouldShowItem(it, q)) continue;
    filtered.push(it);
  }

  if (!filtered.length) {
    empty.style.display = 'block';
    box.innerHTML = '';
    cardStateByKey = new Map();
    lastRenderOrder = [];
    return;
  }
  empty.style.display = 'none';

  const orderKeys = filtered.map((it) => String((it && it.key) || ''));
  let needReorder = false;
  if (orderKeys.length !== lastRenderOrder.length) {
    needReorder = true;
  } else {
    for (let i = 0; i < orderKeys.length; i++) {
      if (orderKeys[i] !== lastRenderOrder[i]) {
        needReorder = true;
        break;
      }
    }
  }
  lastRenderOrder = orderKeys;

  const keep = new Set();

  for (const it of filtered) {
    const cardKey = String(it.key || '');
    keep.add(cardKey);

    let st = cardStateByKey.get(cardKey);
    if (!st) {
      st = createOrderCardState(cardKey);
      cardStateByKey.set(cardKey, st);
    }
    updateCardFromItem(st, it);

    // 保持顺序（仅在顺序变化/新卡时 move；避免每秒移动 DOM 导致“跳动/闪烁”）
    if (needReorder || st.card.parentNode !== box) box.appendChild(st.card);

    const status = String(it.status || '').toLowerCase();
    const order = it.order || {};
    const derived = order.derived || {};
    const memberId = getSelectedMemberId();
    const gid = order.gid ? String(order.gid) : '';
    const mode = derived.mode ? String(derived.mode) : 'live';
    const snapshotCtx = `${memberId}|${mode}|${gid}`;
    if (memberId && gid && shouldRequestTaskSnapshot(status, st, snapshotCtx)) {
      st.lastFollowOddsAt = Date.now();
      st.lastFollowSnapshotCtx = snapshotCtx;
      requestFollowSnapshot({ memberId, gid, mode })
        .then((data) => {
          if (!st || !st.card || st.card.dataset.key !== cardKey) return;
          const simple = data && data.simple && typeof data.simple === 'object' ? data.simple : null;
          if (!simple) return;
          dashboardSnapshotByGame.set(`${mode}|${gid}`, data);
          snapshotErrorByGame.delete(`${mode}|${gid}`);
          st.lastFollowSnapshot = simple;
          renderFollowMarketSnapshot(st.refs.followMarket, order, derived, simple, cardKey);
          renderOrderPool();
        })
        .catch((error) => {
          snapshotErrorByGame.set(`${mode}|${gid}`, error && error.message ? error.message : String(error || 'snapshot_failed'));
          renderOrderPool();
        });
    }

  }

  // 清理已不存在的卡片
  for (const [k, st] of Array.from(cardStateByKey.entries())) {
    if (keep.has(k)) continue;
    try {
      st.card.remove();
    } catch {}
    cardStateByKey.delete(k);
  }
}

async function refreshQueueOnce() {
  if (queueRefreshInFlight) return queueRefreshInFlight;
  const request = (async () => {
    try {
      const data = await fetchJson('/api/follow-queue?limit=60&include_done=1&done_limit=30');
      dashboardQueueItems = Array.isArray(data.items) ? data.items : [];
      dashboardCounts = data.counts && typeof data.counts === 'object' ? data.counts : {};
      lastQueueMeta = { updatedAt: String(data.updated_at || ''), serverTime: String(data.server_time || '') };
      snapshotRevision += 1;
      setText('stServerTime', `服务器时间：${data.server_time || '-'}`);
      const c = data.counts || {};
      const pendingOrders = toCount(c.pending_orders);
      const queueJobs = toCount(c.jobs);
      const doneSuccess = toCount(c.done_ok);
      const doneFailed = toCount(c.done_failed);

      setText('stCounts', `待入队：${pendingOrders} · 队列中：${queueJobs}`);
      setText('stPendingCount', pendingOrders);
      setText('stJobsCount', queueJobs);
      setText('stSuccessCount', doneSuccess);
      setText('stFailedCount', doneFailed);

      const failedCard = $('metricFailedCard');
      if (failedCard) {
        try {
          failedCard.classList.toggle('hasAlert', doneFailed > 0);
        } catch {}
      }

      setText('stQueueUpdatedAt', `队列更新时间：${data.updated_at || '-'}`);
      setText('stLastUpdate', `本页刷新：${fmtNowTime()}`);
      setErrBox('-');
      renderExecutionCenter();
      refreshActiveSnapshots();
    } catch (e) {
      setErrBox(e.message || String(e));
    }
  })();
  queueRefreshInFlight = request;
  try {
    return await request;
  } finally {
    if (queueRefreshInFlight === request) {
      queueRefreshInFlight = null;
    }
  }
}

async function clearFollowQueue() {
  const btn = $('btnClearQueue');
  const pendingText = $('stPendingCount') ? String($('stPendingCount').textContent || '0') : '0';
  const jobsText = $('stJobsCount') ? String($('stJobsCount').textContent || '0') : '0';
  const ok = window.confirm(
    `确认清空跟单队列吗？\n待入队：${pendingText}，队列中：${jobsText}\n仅清空排队订单，不会删除历史结果。`
  );
  if (!ok) return;

  const oldText = btn ? String(btn.textContent || '清空队列') : '清空队列';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '清空中...';
  }
  try {
    const data = await fetchJson('/api/follow-queue/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    await refreshQueueOnce();
    const nPending = toCount(data && data.pending_orders_before);
    const nJobs = toCount(data && data.jobs_before);
    setErrBox(`已清空队列：待入队 ${nPending}，队列中 ${nJobs}`);
    window.setTimeout(() => setErrBox('-'), 1800);
  } catch (e) {
    setErrBox(`清空失败：${e.message || String(e)}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }
}

function restartTimers() {
  if (queueTimer) clearInterval(queueTimer);
  queueTimer = null;

  const ms = Math.max(300, Math.min(10000, readNumber('inQueueRefresh', 1000)));
  queueTimer = setInterval(refreshQueueOnce, ms);
}

function bindUi() {
  const focusTaskTab = (filter) => {
    const button = document.querySelector(`.taskTab[data-task-filter="${filter}"]`);
    if (button) button.click();
  };
  const runningSummary = document.querySelector('.commandBar .runningMetric');
  const waitingSummary = document.querySelector('.commandBar .waitingMetric');
  if (runningSummary) runningSummary.addEventListener('click', () => focusTaskTab('running'));
  if (waitingSummary) waitingSummary.addEventListener('click', () => focusTaskTab('waiting'));
  if ($('btnRefreshNow')) $('btnRefreshNow').addEventListener('click', () => refreshQueueOnce());
  if ($('btnClearQueue')) $('btnClearQueue').addEventListener('click', () => clearFollowQueue());
  $('selMember').addEventListener('change', async () => {
    resetOddCache();
    snapshotCache = new Map();
    snapshotInflight = new Map();
    dashboardSnapshotByGame = new Map();
    snapshotErrorByGame = new Map();
    await refreshMemberStatus();
    await refreshQueueOnce();
  });
  $('inQueueRefresh').addEventListener('change', () => restartTimers());
  $('inQueueRefresh').addEventListener('input', () => restartTimers());
  $('ckOnlyActive').addEventListener('change', () => renderExecutionCenter());
  if ($('inSearch')) $('inSearch').addEventListener('input', () => renderOrderPool());
  if ($('ckOnlyErrors')) $('ckOnlyErrors').addEventListener('change', () => renderExecutionLog());
  if ($('selLogSplit')) $('selLogSplit').addEventListener('change', (event) => {
    activeLogSplit = String(event.target.value || 'all');
    renderExecutionLog();
  });
  for (const button of document.querySelectorAll('.logFilter')) {
    button.addEventListener('click', () => {
      activeLogFilter = String(button.dataset.logFilter || 'all');
      for (const item of document.querySelectorAll('.logFilter')) item.classList.toggle('active', item === button);
      renderExecutionLog();
    });
  }
  $('boxQueue').addEventListener('click', (event) => {
    const details = event.target && event.target.closest ? event.target.closest('.orderDetailsToggle') : null;
    if (details) {
      const group = details.closest('.orderGroup');
      if (!group) return;
      const focused = group.classList.toggle('detailsFocus');
      if (focused && group.classList.contains('isCollapsed')) {
        group.classList.remove('isCollapsed');
        const toggleButton = group.querySelector('.orderToggle');
        if (toggleButton) {
          toggleButton.title = '收起订单';
          toggleButton.setAttribute('aria-label', '收起订单');
          const toggleIcon = toggleButton.querySelector('i');
          if (toggleIcon) toggleIcon.className = 'fa-solid fa-chevron-up';
        }
      }
      details.setAttribute('aria-pressed', focused ? 'true' : 'false');
      if (focused) {
        const summary = group.querySelector('.orderSummary');
        if (summary && typeof summary.scrollIntoView === 'function') summary.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
      return;
    }
    const toggle = event.target && event.target.closest ? event.target.closest('.orderToggle') : null;
    if (!toggle) return;
    const group = toggle.closest('.orderGroup');
    if (!group) return;
    const collapsed = group.classList.toggle('isCollapsed');
    toggle.title = collapsed ? '展开订单' : '收起订单';
    toggle.setAttribute('aria-label', collapsed ? '展开订单' : '收起订单');
    const icon = toggle.querySelector('i');
    if (icon) icon.className = `fa-solid ${collapsed ? 'fa-chevron-down' : 'fa-chevron-up'}`;
  });
  for (const button of document.querySelectorAll('.taskTab')) {
    button.addEventListener('click', () => {
      activeTaskFilter = String(button.dataset.taskFilter || 'all');
      renderOrderPool();
    });
  }

  const dirtyInputs = [
    'inSplitCount',
    'inSplitDelay',
    'inWaitLineNotFound',
  ];
  for (const id of dirtyInputs) {
    const el = $(id);
    if (!el) continue;
    el.addEventListener('input', updateConfigDirtyState);
    el.addEventListener('change', updateConfigDirtyState);
  }
  if ($('btnSaveConfig')) $('btnSaveConfig').addEventListener('click', () => saveFollowSettings());
  if ($('btnResetConfig')) $('btnResetConfig').addEventListener('click', () => resetFollowSettingsToRecommended());
}

async function main() {
  bindUi();
  await loadFollowSettings();
  await refreshMemberStatus();
  await Promise.all([refreshQueueOnce(), refreshExecutionResults()]);

  statusTimer = setInterval(() => {
    refreshMemberStatus();
    refreshExecutionResults();
  }, 4000);
  restartTimers();
}

main().catch((e) => {
  setErrBox(e.message || String(e));
});
