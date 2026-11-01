(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.HDArsenalCards = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function text(value, fallback = '-') {
    const raw = String(value == null ? '' : value).trim();
    return raw || fallback;
  }

  function memberLabel(member) {
    return text((member && (member.label || member.username)) || '', '-');
  }

  function platformHost(baseUrl) {
    const raw = text(baseUrl, '-');
    if (raw === '-') return raw;
    try {
      const parsed = new URL(raw);
      return parsed.host || raw;
    } catch (_err) {
      return raw;
    }
  }

  function normalizedStatus(status) {
    return String(status || 'unknown').trim().toLowerCase();
  }

  function numberValue(value) {
    if (value == null || value === '') return 0;
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function formatBalance(value) {
    const n = numberValue(value);
    if (!Number.isFinite(n) || n <= 0) return '0';
    if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n));
    return String(Number(n.toFixed(1)));
  }

  function formatDelta(value) {
    const n = numberValue(value);
    if (!Number.isFinite(n) || n <= 0) return '0';
    if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n));
    return String(Number(n.toFixed(1)));
  }

  function providerText(member) {
    return String(member && member.session_provider || 'native').trim().toLowerCase() === 'adspower'
      ? 'AdsPower'
      : '原生登录';
  }

  function canAutoRelogin(member) {
    if (!member) return false;
    if (member.can_auto_relogin != null) return !!member.can_auto_relogin;
    const autoRelogin = member.auto_relogin !== false;
    const provider = String(member.session_provider || 'native').trim().toLowerCase();
    if (!autoRelogin) return false;
    if (provider === 'adspower') {
      return !!(String(member.adspower_profile_id || '').trim() || String(member.adspower_profile_no || '').trim());
    }
    return !!(String(member.base_url || '').trim() && String(member.username || '').trim() && String(member.password || '').trim());
  }

  function normalizedUsage(member) {
    const raw = String(member && member.usage || '').trim().toLowerCase();
    if (raw === 'read') return 'read';
    if (raw === 'bet') return 'bet';
    return String(member && member.role || '').trim().toLowerCase() === 'quote' ? 'read' : 'bet';
  }

  function usageText(member) {
    return normalizedUsage(member) === 'read' ? '拉数据用' : '跟单用';
  }

  function computePoolEligibility(member, options) {
    const current = member || {};
    const status = normalizedStatus(current.status);
    const balance = numberValue(current.balance != null ? current.balance : (current.maxcredit || current.nowcredit || current.cash));
    const autoRelogin = canAutoRelogin(current);

    if (status === 'maintenance') {
      return { tone: 'maintenance', text: '平台维护暂停', balance, threshold: 0, canJoin: false };
    }
    if (status === 'recovering') {
      return { tone: 'recovering', text: '恢复验证中', balance, threshold: 0, canJoin: false };
    }
    if (status === 'relogin') {
      return { tone: 'recovering', text: '自动恢复中', balance, threshold: 0, canJoin: false };
    }
    if (status === 'online') {
      if (balance <= 0) {
        return { tone: 'low', text: '余额待补', balance, threshold: 0, canJoin: false };
      }
      return { tone: 'ready', text: '自动候选中', balance, threshold: 0, canJoin: true };
    }
    if (autoRelogin) {
      return { tone: 'recovering', text: '离线待恢复', balance, threshold: 0, canJoin: false };
    }
    return { tone: 'manual', text: '需人工处理', balance, threshold: 0, canJoin: false };
  }

  function statusModel(status) {
    const raw = normalizedStatus(status);
    if (raw === 'online') return { statusText: '在线', statusTone: 'online' };
    if (raw === 'offline') return { statusText: '离线', statusTone: 'offline' };
    if (raw === 'relogin') return { statusText: '重登中', statusTone: 'warning' };
    if (raw === 'maintenance') return { statusText: '平台维护', statusTone: 'maintenance' };
    if (raw === 'recovering') return { statusText: '恢复验证中', statusTone: 'warning' };
    return { statusText: '未知', statusTone: 'muted' };
  }

  function computeArsenalMemberStats(members, options) {
    const list = Array.isArray(members) ? members.filter(Boolean) : [];
    const online = list.filter((member) => normalizedStatus(member.status) === 'online').length;
    const maintenance = list.filter((member) => normalizedStatus(member.status) === 'maintenance').length;
    const stats = {
      total: list.length,
      online,
      offline: Math.max(0, list.length - online - maintenance),
      maintenance,
      ready: 0,
      lowBalance: 0,
      recovering: 0,
      manual: 0,
      totalBalance: 0,
    };
    list.forEach((member) => {
      const eligibility = computePoolEligibility(member, options || {});
      stats.totalBalance += numberValue(eligibility.balance);
      if (eligibility.tone === 'ready') stats.ready += 1;
      else if (eligibility.tone === 'low') stats.lowBalance += 1;
      else if (eligibility.tone === 'recovering') stats.recovering += 1;
      else if (eligibility.tone === 'manual') stats.manual += 1;
    });
    stats.totalBalance = Math.round(stats.totalBalance * 10) / 10;
    return stats;
  }

  function filterPoolMembers(members, options) {
    const list = Array.isArray(members) ? members.filter(Boolean) : [];
    const filter = String(options && options.filter || 'all').trim().toLowerCase() || 'all';
    if (filter === 'all') return list;
    return list.filter((member) => computePoolEligibility(member, options || {}).tone === filter);
  }

  function proxyText(member) {
    return text(member && (member.proxy_ip || member.proxy_label), '未配置代理');
  }

  function geoText(member) {
    const geo = member && member.proxy_geo && member.proxy_geo.ok ? member.proxy_geo : null;
    if (!geo) return '未识别地区';
    return [geo.country, geo.region].map((v) => text(v, '')).filter(Boolean).join(' ') || '未识别地区';
  }

  function buildArsenalCardModel(member, options) {
    const current = member || {};
    const opts = options || {};
    const status = statusModel(current.status);
    const mappingLabel = text(opts.mappingLabel, '全部跟单');
    const mappingTarget = text(opts.mappingTarget, '');
    const eligibility = computePoolEligibility(current, opts);
    const autoRelogin = canAutoRelogin(current);
    const autoReloginText = status.statusTone === 'maintenance'
      ? '维护后自动验证'
      : status.statusTone === 'warning'
      ? '重登中'
      : autoRelogin
        ? '自动重登'
        : '需人工处理';
    const lastError = text(current.last_error, '');
    const balance = eligibility.balance;
    let balanceStateText = '系统会按订单金额自动筛选';
    if (eligibility.tone === 'ready') {
      balanceStateText = '系统会按订单金额自动筛选';
    } else if (eligibility.tone === 'low') {
      balanceStateText = '当前余额不足，暂不进入候选';
    } else if (eligibility.tone === 'recovering') {
      balanceStateText = '恢复后自动回到候选池';
    } else if (eligibility.tone === 'maintenance') {
      balanceStateText = '平台恢复后自动验证原会话';
    } else if (eligibility.tone === 'manual') {
      balanceStateText = '需要人工处理后恢复';
    }
    return {
      id: text(current.id, ''),
      label: memberLabel(current),
      platformUrl: text(current.base_url, '-'),
      platformHost: platformHost(current.base_url),
      statusText: status.statusText,
      statusTone: status.statusTone,
      proxyText: proxyText(current),
      geoText: geoText(current),
      mappingText: mappingLabel,
      selectValue: mappingTarget,
      usageValue: normalizedUsage(current),
      usageText: usageText(current),
      statusMetaText: `${status.statusText} · ${providerText(current)}`,
      balanceValue: balance,
      balanceText: formatBalance(balance),
      balanceStateText,
      balanceUpdatedAt: numberValue(current.balance_updated_at || current.last_ok_at),
      providerText: providerText(current),
      autoReloginText,
      canAutoRelogin: autoRelogin,
      eligibilityTone: eligibility.tone,
      eligibilityText: eligibility.text,
      canJoinRandomPool: !!eligibility.canJoin,
      threshold: eligibility.threshold,
      lastError: lastError || '',
      requestFailCount: Number(current.request_fail_count || 0) || 0,
      reloginFailCount: Number(current.relogin_fail_count || 0) || 0,
      maintenanceStartAt: numberValue(current.maintenance_start_at),
      maintenanceUntil: numberValue(current.maintenance_until),
    };
  }

  return {
    buildArsenalCardModel,
    computeArsenalMemberStats,
    computePoolEligibility,
    filterPoolMembers,
  };
});
