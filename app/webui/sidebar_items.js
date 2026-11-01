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
  const LOCAL_BRIDGE_FILTER_TARGET = '__local_bridge__';

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

  function isLocalBridgeOrder(row) {
    return !!(row && normalizeName(row._signal_origin) === 'local_bridge');
  }

  function isRemoteSignalOrder(row) {
    if (!row || isLocalBridgeOrder(row)) return false;
    return !!(
      row._skip_signal_forward === true ||
      normalizeName(row._signal_origin) === 'remote_signal_pull'
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

  function normalizeLocalBridgeConfig(localBridgeConfig) {
    const root = localBridgeConfig && typeof localBridgeConfig === 'object' ? localBridgeConfig : {};
    const cfg = root.config && typeof root.config === 'object' ? root.config : root;
    const status = root.status && typeof root.status === 'object'
      ? root.status
      : (cfg.status && typeof cfg.status === 'object' ? cfg.status : {});
    return {
      configured: !!cfg.local_bridge_configured,
      enabled: !!cfg.local_bridge_enabled,
      name: normalizeName(cfg.local_bridge_name) || '本地桥接',
      target: normalizeName(cfg.local_bridge_target),
      status,
    };
  }

  function buildLocalBridgeItems(localBridgeConfig, orders) {
    const cfg = normalizeLocalBridgeConfig(localBridgeConfig);
    if (!cfg.configured) return [];
    const rows = (Array.isArray(orders) ? orders : []).filter(isLocalBridgeOrder);
    const stats = buildOrderStats(rows);
    const status = cfg.status || {};
    const lastReceivedMs = Math.round(Number(status.last_received_at || 0) * 1000) || 0;
    const online = cfg.enabled && !!status.online;
    const hasReceived = lastReceivedMs > 0 || stats.count > 0;
    const countText = stats.count > 0 ? `${stats.count} 条对手盘` : '暂无对手盘';
    return [
      {
        key: `local-bridge:${cfg.target || 'default'}`,
        kind: 'signal-source',
        isLocalBridge: true,
        filterTarget: LOCAL_BRIDGE_FILTER_TARGET,
        canDelete: true,
        deleteEndpoint: '/api/local-bridge-config/delete',
        name: cfg.name,
        badge: '本地桥接',
        statusClass: online ? 'bg-cyan-400 animate-pulse' : cfg.enabled ? 'bg-cyan-400' : 'bg-slate-500',
        statusText: !cfg.enabled ? '已暂停' : online ? '已接入' : hasReceived ? '已接收 · 等待下一条' : '等待信号',
        metaText: `${cfg.target || 'ps3838'} · ${countText}`,
        latestTs: Math.max(stats.latestTs, Math.floor(lastReceivedMs / 1000)),
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
    const localBridgeConfig = (input && input.localBridgeConfig) || {};
    const accountTotal = accounts.filter((acc) => normalizeName(acc && acc.name)).length;
    const onlineAccounts = accounts.filter((acc) => {
      const name = normalizeName(acc && acc.name);
      return name && statuses[name] && statuses[name].status === 'online';
    }).length;
    const signalTotal = hasConfiguredSignalSource(signalPullConfig) ? 1 : 0;
    const onlineSignals = isSignalSourceOnline(signalPullConfig) ? 1 : 0;
    const bridge = normalizeLocalBridgeConfig(localBridgeConfig);
    const bridgeTotal = bridge.configured ? 1 : 0;
    const onlineBridge = bridge.configured && bridge.enabled ? 1 : 0;
    const total = accountTotal + signalTotal + bridgeTotal;
    const online = onlineAccounts + onlineSignals + onlineBridge;
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
      ...buildLocalBridgeItems(input && input.localBridgeConfig, orders),
    ];
  }

  return {
    REMOTE_SIGNAL_FILTER_TARGET,
    LOCAL_BRIDGE_FILTER_TARGET,
    buildSidebarItems,
    computeTradingNodeStatus,
  };
});
