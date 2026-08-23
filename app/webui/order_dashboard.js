(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.HDOrderDashboard = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const DEFAULT_ORDER_DASHBOARD_FILTERS = {
    keyword: '',
    timeRange: 'today',
    orderType: '',
    status: '',
    playCategory: '',
    league: '',
    amountBucket: '',
    sortBy: 'time-desc',
  };

  const AMOUNT_BUCKETS = [
    { label: '0-1k', min: 0, max: 1000 },
    { label: '1k-3k', min: 1000, max: 3000 },
    { label: '3k-5k', min: 3000, max: 5000 },
    { label: '5k+', min: 5000, max: Infinity },
  ];

  function normalizeText(value) {
    return String(value || '').trim();
  }

  function toNumber(value) {
    const n = Number(String(value == null ? '' : value).replace(/,/g, ''));
    return Number.isFinite(n) ? n : 0;
  }

  function getOrderTimestampMs(order) {
    const ts = toNumber(order && order.ts);
    if (ts > 0) return ts * 1000;
    const createdAt = Date.parse(normalizeText(order && (order.created_at || order.createdAt)));
    return Number.isFinite(createdAt) ? createdAt : 0;
  }

  function getDayStartMs(nowMs) {
    const d = new Date(nowMs);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }

  function getTimeRangeBounds(timeRange, nowMs) {
    const now = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
    if (timeRange === 'history') return null;
    if (timeRange === 'week') return { start: now - 7 * 86400 * 1000, end: now + 1 };
    return { start: getDayStartMs(now), end: now + 1 };
  }

  function getOrderStake(order) {
    return toNumber(order && (order.stake ?? order.amount ?? order.followAmount));
  }

  function getOrderType(order) {
    const raw = (order && order.raw && typeof order.raw === 'object') ? order.raw : {};
    const candidates = [
      order && order.orderType,
      order && order.order_type,
      raw.orderType,
      raw.ORDER_TYPE,
      raw.BALL_ACT,
      raw.MARKET,
    ].map(normalizeText).filter(Boolean);
    const text = candidates.join(' ');
    if (/滚球|live|rb/i.test(text)) return '滚球';
    if (/早盘|pre|early|today/i.test(text)) return '早盘';
    return '早盘';
  }

  function getOrderStatus(order) {
    const raw = (order && order.raw && typeof order.raw === 'object') ? order.raw : {};
    const value = normalizeText(order && (order.status_label || order.status || raw.status_label || raw.status || raw.BALL_ACT || raw.ORDER_STATUS));
    if (!value) return '未知';
    if (/待确认|待处理|pending|unknown/i.test(value)) return '待确认';
    if (/失败|拒绝|fail|error/i.test(value)) return '失败';
    if (/成功|已接收|已确认|已投注|ok|success/i.test(value)) return '已接收';
    return value;
  }

  function getOrderPlayCategory(order) {
    const text = [
      order && order.marketLabel,
      order && order.market_label,
      order && order.betType,
      order && order.line,
      order && order.selection,
    ].map(normalizeText).join(' ').toLowerCase();
    if (text.includes('handicap') || text.includes('让球') || text.includes('让分') || text.includes('亚盘')) return '让球';
    if (text.includes('over') || text.includes('under') || text.includes('大球') || text.includes('小球') || /(^|\s)[大小](\s|$)/.test(text)) return '大小球';
    if (text.includes('moneyline') || text.includes('独赢') || text.includes('胜平负') || text.includes('1x2')) return '独赢';
    return normalizeText(order && order.betType) || '其他';
  }

  function getAmountBucket(amount) {
    const n = getOrderStake({ stake: amount });
    const bucket = AMOUNT_BUCKETS.find((item) => n >= item.min && n < item.max);
    return bucket ? bucket.label : AMOUNT_BUCKETS[AMOUNT_BUCKETS.length - 1].label;
  }

  function orderSearchText(order) {
    return [
      order && (order.orderId || order.order_id),
      order && order.account,
      order && order.target,
      order && order.league,
      order && order.sides,
      order && order.match_name,
      order && order.betType,
      order && order.line,
      order && order.selection,
    ].map(normalizeText).filter(Boolean).join(' ').toLowerCase();
  }

  function shouldKeepOrder(order, filters, options) {
    const current = Object.assign({}, DEFAULT_ORDER_DASHBOARD_FILTERS, filters || {});
    const keyword = normalizeText(current.keyword).toLowerCase();
    if (keyword && !orderSearchText(order).includes(keyword)) return false;
    if (current.league && normalizeText(order && order.league) !== current.league) return false;
    if (current.orderType && getOrderType(order) !== current.orderType) return false;
    if (current.status && getOrderStatus(order) !== current.status) return false;
    if (current.playCategory && getOrderPlayCategory(order) !== current.playCategory) return false;
    if (current.amountBucket && getAmountBucket(getOrderStake(order)) !== current.amountBucket) return false;

    const bounds = getTimeRangeBounds(current.timeRange, options && options.nowMs);
    if (bounds) {
      const ts = getOrderTimestampMs(order);
      if (!ts || ts < bounds.start || ts >= bounds.end) return false;
    }
    return true;
  }

  function sortDashboardOrders(orders, sortBy) {
    const rows = [...(Array.isArray(orders) ? orders : [])];
    if (sortBy === 'time-asc') return rows.sort((a, b) => getOrderTimestampMs(a) - getOrderTimestampMs(b));
    if (sortBy === 'amount-desc') return rows.sort((a, b) => getOrderStake(b) - getOrderStake(a));
    if (sortBy === 'amount-asc') return rows.sort((a, b) => getOrderStake(a) - getOrderStake(b));
    return rows.sort((a, b) => getOrderTimestampMs(b) - getOrderTimestampMs(a));
  }

  function filterAndSortOrders(orders, filters, options) {
    const current = Object.assign({}, DEFAULT_ORDER_DASHBOARD_FILTERS, filters || {});
    return sortDashboardOrders((Array.isArray(orders) ? orders : []).filter((order) => shouldKeepOrder(order, current, options || {})), current.sortBy);
  }

  function countBy(rows, keyFn) {
    const map = new Map();
    for (const row of rows) {
      const label = keyFn(row) || '未分类';
      const prev = map.get(label) || { label, count: 0, stake: 0 };
      prev.count += 1;
      prev.stake += getOrderStake(row);
      map.set(label, prev);
    }
    return [...map.values()].sort((a, b) => b.count - a.count || b.stake - a.stake || a.label.localeCompare(b.label, 'zh-CN'));
  }

  function buildAmountDistribution(rows) {
    const map = new Map(AMOUNT_BUCKETS.map((bucket) => [bucket.label, { label: bucket.label, total: 0, sizeCount: 0, handicapCount: 0, otherCount: 0 }]));
    for (const row of rows) {
      const bucket = map.get(getAmountBucket(getOrderStake(row)));
      if (!bucket) continue;
      bucket.total += 1;
      const category = getOrderPlayCategory(row);
      if (category === '大小球') bucket.sizeCount += 1;
      else if (category === '让球') bucket.handicapCount += 1;
      else bucket.otherCount += 1;
    }
    return AMOUNT_BUCKETS.map((bucket) => map.get(bucket.label));
  }

  function uniqueSorted(rows, keyFn) {
    return [...new Set(rows.map(keyFn).map(normalizeText).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  }

  function buildOrderDashboardModel(orders, filters, options) {
    const source = Array.isArray(orders) ? orders : [];
    const current = Object.assign({}, DEFAULT_ORDER_DASHBOARD_FILTERS, filters || {});
    const filtered = filterAndSortOrders(source, current, options || {});
    const totalStake = filtered.reduce((sum, row) => sum + getOrderStake(row), 0);
    return {
      filters: {
        leagues: uniqueSorted(source, (row) => row && row.league),
        playCategories: uniqueSorted(source, getOrderPlayCategory),
      },
      stats: {
        totalOrders: filtered.length,
        liveOrders: filtered.filter((row) => getOrderType(row) === '滚球').length,
        earlyOrders: filtered.filter((row) => getOrderType(row) === '早盘').length,
        totalStake,
        averageStake: filtered.length ? totalStake / filtered.length : 0,
      },
      charts: {
        playDistribution: countBy(filtered, getOrderPlayCategory),
        leagueDistribution: countBy(filtered, (row) => normalizeText(row && row.league) || '未分类').slice(0, 8),
        amountDistribution: buildAmountDistribution(filtered),
      },
      orders: filtered,
    };
  }

  function formatDashboardMoney(value) {
    const n = Math.floor(toNumber(value));
    if (n >= 1000) return `¥${(n / 1000).toFixed(1)}k`;
    return `¥${n}`;
  }

  return {
    AMOUNT_BUCKETS,
    DEFAULT_ORDER_DASHBOARD_FILTERS,
    buildOrderDashboardModel,
    filterAndSortOrders,
    formatDashboardMoney,
    getAmountBucket,
    getOrderPlayCategory,
    getOrderStake,
    getOrderStatus,
    getOrderType,
  };
});
