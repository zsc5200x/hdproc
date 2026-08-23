(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.HDSidebarItems = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const REMOTE_SIGNAL_FILTER_TARGET = '__remote_signal_pull__';

  function normalizeName(value) {
    return String(value || '').trim();
  }

  function uniqueNames(values) {
    const out = [];
    const seen = new Set();
    for (const value of Array.isArray(values) ? values : []) {
      const name = normalizeName(value);
      if (!name || seen.has(name)) continue;
      seen.add(name);
      out.push(name);
    }
    return out;
  }

  function getAccountState(statuses, name) {
    const state = (statuses && statuses[name] && statuses[name].status) || 'offline';
    if (state === 'online') {
      return { statusClass: 'bg-green-500 animate-pulse', statusText: '在线' };
    }
    if (state === 'relogin') {
      return { statusClass: 'bg-yellow-500', statusText: '重登中' };
    }
    return { statusClass: 'bg-slate-500', statusText: '离线' };
  }

  function buildAccountItems(accounts, statuses) {
    return (Array.isArray(accounts) ? accounts : []).map((acc) => {
      const name = normalizeName(acc && acc.name);
      const state = getAccountState(statuses, name);
      return {
        key: `account:${name}`,
        kind: 'account',
        name,
        badge: '账号节点',
        statusClass: state.statusClass,
        statusText: state.statusText,
        metaText: '',
        latestTs: 0,
      };
    });
  }

  function isRemoteSignalOrder(row) {
    return !!(
      row &&
      (
        row._skip_signal_forward === true ||
        normalizeName(row._signal_origin) === 'remote_signal_pull'
      )
    );
  }

  function buildOrderStats(orders) {
    let count = 0;
    let latestTs = 0;
    for (const row of Array.isArray(orders) ? orders : []) {
      const ts = Number((row && row.ts) || 0) || 0;
      count += 1;
      if (ts > latestTs) latestTs = ts;
    }
    return { count, latestTs };
  }

  function buildSignalOrderStats(orders) {
    const rows = Array.isArray(orders) ? orders : [];
    const remoteRows = rows.filter(isRemoteSignalOrder);
    return buildOrderStats(remoteRows.length > 0 ? remoteRows : rows);
  }

  function parseSignalSourceLabel(url) {
    const raw = normalizeName(url);
    if (!raw) return '';
    try {
      const parsed = new URL(raw);
      return parsed.host || raw;
    } catch (_err) {
      return raw;
    }
  }

  function buildSignalSourceItems(signalPullConfig, orders) {
    const cfgRoot = signalPullConfig && typeof signalPullConfig === 'object' ? signalPullConfig : {};
    const cfg = cfgRoot.config && typeof cfgRoot.config === 'object' ? cfgRoot.config : cfgRoot;
    const url = normalizeName(cfg.signal_pull_url || cfg.url);
    if (!url) return [];
    const sourceLabel = parseSignalSourceLabel(url);
    const stats = buildSignalOrderStats(orders);
    const countText = stats.count > 0 ? `${stats.count} 条信号` : '暂无信号';
    const enabled = 'signal_pull_enabled' in cfg ? !!cfg.signal_pull_enabled : !!cfg.enabled;
    return [
      {
        key: `signal-source:${sourceLabel || 'remote'}`,
        kind: 'signal-source',
        filterTarget: REMOTE_SIGNAL_FILTER_TARGET,
        canDelete: true,
        deleteEndpoint: '/api/signal-pull-config/delete',
        name: '远端交易节点',
        badge: '远端交易节点',
        statusClass: enabled ? 'bg-cyan-400 animate-pulse' : 'bg-slate-500',
        statusText: enabled ? '已接入' : '已配置未启用',
        metaText: sourceLabel ? `${sourceLabel} · ${countText}` : countText,
        latestTs: stats.latestTs,
      },
    ];
  }

  function normalizeSignalPullConfig(signalPullConfig) {
    const cfgRoot = signalPullConfig && typeof signalPullConfig === 'object' ? signalPullConfig : {};
    return cfgRoot.config && typeof cfgRoot.config === 'object' ? cfgRoot.config : cfgRoot;
  }

  function hasConfiguredSignalSource(signalPullConfig) {
    const cfg = normalizeSignalPullConfig(signalPullConfig);
    return !!normalizeName(cfg.signal_pull_url || cfg.url);
  }

  function isSignalSourceOnline(signalPullConfig) {
    const cfg = normalizeSignalPullConfig(signalPullConfig);
    const hasEnabled = Object.prototype.hasOwnProperty.call(cfg, 'signal_pull_enabled');
    return hasConfiguredSignalSource(signalPullConfig) && (hasEnabled ? !!cfg.signal_pull_enabled : !!cfg.enabled);
  }

  function computeTradingNodeStatus(input) {
    const accounts = Array.isArray(input && input.accounts) ? input.accounts : [];
    const statuses = (input && input.statuses) || {};
    const signalPullConfig = (input && input.signalPullConfig) || {};
    const accountTotal = accounts.filter((acc) => normalizeName(acc && acc.name)).length;
    const onlineAccounts = accounts.filter((acc) => {
      const name = normalizeName(acc && acc.name);
      return name && statuses[name] && statuses[name].status === 'online';
    }).length;
    const signalTotal = hasConfiguredSignalSource(signalPullConfig) ? 1 : 0;
    const onlineSignals = isSignalSourceOnline(signalPullConfig) ? 1 : 0;
    const total = accountTotal + signalTotal;
    const online = onlineAccounts + onlineSignals;
    return {
      online,
      total,
      label: `${online} / ${total}`,
    };
  }

  function buildSidebarItems(input) {
    const accounts = Array.isArray(input && input.accounts) ? input.accounts : [];
    const statuses = (input && input.statuses) || {};
    const signalPullConfig = (input && input.signalPullConfig) || {};
    const orders = Array.isArray(input && input.orders) ? input.orders : [];
    return [
      ...buildAccountItems(accounts, statuses),
      ...buildSignalSourceItems(signalPullConfig, orders),
    ];
  }

  return {
    REMOTE_SIGNAL_FILTER_TARGET,
    buildSidebarItems,
    computeTradingNodeStatus,
  };
});
