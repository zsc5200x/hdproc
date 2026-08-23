(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.HDReportCards = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function toFiniteNumber(value) {
    const n = Number(String(value == null ? '' : value).replace(/,/g, ''));
    return Number.isFinite(n) ? n : 0;
  }

  function formatCompactMoney(value) {
    const amount = Math.floor(toFiniteNumber(value));
    if (amount <= 0) return '—';
    return amount >= 1000 ? `¥${(amount / 1000).toFixed(1)}k` : `¥${amount}`;
  }

  function formatSignedMoney(value) {
    const n = toFiniteNumber(value);
    const abs = Math.abs(n).toFixed(2);
    return `${n >= 0 ? '+' : '-'}¥${abs}`;
  }

  function compactText(value) {
    return String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  }

  function firstText() {
    for (const value of arguments) {
      const text = compactText(value);
      if (text) return text;
    }
    return '';
  }

  function parseBetDisplay(source) {
    const current = source || {};
    const betTypeRaw = String(current.betType || '').trim();
    const lineText = String(current.display_line || current.line || '').trim();
    const orderKind = String(current.orderKind || '').trim() || (/滚球/i.test(betTypeRaw) ? '滚球' : '早盘');
    const normalized = betTypeRaw
      .replace(/[()（）]/g, ' ')
      .replace(/-/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const low = normalized.toLowerCase();
    const isTotal = /大\s*\/\s*小|over\s*\/\s*under|over|under|大小/.test(low);
    const period = /下半场|2nd|second half|\b2h\b/i.test(betTypeRaw)
      ? '下半场'
      : (/上半场|半场|1st|first half|\b1h\b/i.test(betTypeRaw) ? '上半场' : '全场');

    let marketName = '盘口';
    let betLabel = betTypeRaw.slice(0, 6) || 'OTHER';
    if (/card|罚牌|牌数/i.test(betTypeRaw)) {
      marketName = '罚牌数';
      betLabel = isTotal ? '罚牌大小' : '罚牌';
    } else if (/corner|角球/i.test(betTypeRaw)) {
      marketName = '角球数';
      betLabel = isTotal ? '角球大小' : '角球';
    } else if (/(handicap|让球|让分|亚盘)/i.test(betTypeRaw)) {
      marketName = '让球';
      betLabel = '让球';
    } else if (/(moneyline|独赢|胜平负|1x2)/i.test(betTypeRaw)) {
      marketName = '独赢';
      betLabel = '独赢';
    } else if (isTotal || /大球|小球|进球/i.test(betTypeRaw)) {
      marketName = '进球数';
      betLabel = '进球大小';
    } else if (/over|大/i.test(betTypeRaw)) {
      marketName = '进球数';
      betLabel = '大球';
    } else if (/under|小/i.test(betTypeRaw)) {
      marketName = '进球数';
      betLabel = '小球';
    }

    const selection = String(current.selection || '').trim();
    const selectionBase = [
      selection,
      lineText && !selection.includes(lineText) ? lineText : '',
    ].filter(Boolean).join(' ') || lineText;
    const selectionText = selectionBase
      ? `${selectionBase}${current.odds ? ` @ ${current.odds}` : ''}`
      : (betLabel || '--');
    const explicitMarket = String(current.marketLabel || current.market_label || '').trim();
    const marketText = [orderKind, period, explicitMarket || marketName].filter(Boolean).join(' · ');

    return {
      betLabel,
      marketName: explicitMarket || marketName,
      marketText,
      orderKind,
      period,
      selectionText,
    };
  }

  function nestedText(source, path) {
    let cur = source || {};
    for (const key of path) {
      if (!cur || typeof cur !== 'object') return '';
      cur = cur[key];
    }
    return compactText(cur);
  }

  function formatFollowStake(value) {
    const amount = toFiniteNumber(value);
    if (amount <= 0) return '—';
    return amount >= 1000 ? `¥${(amount / 1000).toFixed(1)}k` : `¥${Math.floor(amount)}`;
  }

  function followTicketId(row) {
    const r = row || {};
    return firstText(
      r.ticket_id,
      r.ticketId,
      nestedText(r, ['bet_result_head', 'ticket_id']),
      nestedText(r, ['bet_head', 'ticket_id']),
      nestedText(r, ['response_head', 'ticket_id']),
    );
  }

  function followFailureReason(row) {
    const r = row || {};
    return firstText(
      r.message,
      r.error_message,
      r.last_message,
      nestedText(r, ['bet_result_head', 'errormsg']),
      nestedText(r, ['bet_head', 'errormsg']),
      nestedText(r, ['order_view_head', 'errormsg']),
      r.exception,
      r.error_type,
    ) || '失败原因未返回';
  }

  function settlementTone(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'win') return 'win';
    if (s === 'lose') return 'lose';
    return 'pending';
  }

  function buildSelectionText(order, betLabel) {
    return parseBetDisplay(Object.assign({}, order || {}, { betType: (order || {}).betType || betLabel || '' })).selectionText;
  }

  function buildReportStatusText(summary) {
    const current = summary || {};
    if (current.engineStopped && Number(current.ok || 0) <= 0 && Number(current.fail || 0) <= 0 && Number(current.pending || 0) > 0) {
      return '跟单引擎未运行';
    }
    const parts = [`成功 ${Number(current.ok || 0)}`, `失败 ${Number(current.fail || 0)}`];
    if (Number(current.pending || 0) > 0) parts.push(`待处理 ${Number(current.pending || 0)}`);
    return parts.join(' · ');
  }

  function buildReportCardModel(input) {
    const current = input || {};
    const order = current.order || {};
    const oid = String(order.orderId || order.order_id || current.orderId || '').trim();
    const settle = current.settlement || {};
    const tone = settlementTone(settle.status);
    const profit = toFiniteNumber(settle.profit_sum);
    const showProfit = tone === 'win' || tone === 'lose';
    const orderKind = String(current.orderKind || '实时');
    const betDisplay = parseBetDisplay(Object.assign({}, order, { orderKind }));
    return {
      cardClass: 'match-card rpt-card rpt-order-card',
      headerChip: `#${oid || '—'}`,
      orderId: oid,
      league: String(order.league || '未知联赛'),
      matchup: String(order.sides || order.match_name || '—'),
      betLabel: String(current.betLabel || betDisplay.betLabel || order.betType || ''),
      betColor: String(current.betColor || '#3B82F6'),
      marketText: betDisplay.marketText || '盘口信息',
      selectionText: betDisplay.selectionText || buildSelectionText(order, current.betLabel),
      statusText: String(order.status_label || order.status || '已接收'),
      reportStatusText: buildReportStatusText(current.followSummary),
      eventId: String(current.eventId || order.eventId || order.event_id || order.gid || '-'),
      stakeText: formatCompactMoney(current.followStake),
      oddsText: `@ ${String(order.odds || '—')}`,
      account: String(current.account || '-'),
      source: String(current.source || order.target || '-'),
      timeText: String(current.timeText || '-'),
      orderKind,
      isLiveOrder: !!current.isLiveOrder,
      settlementTone: tone,
      settlementLabel: String(settle.status_label || '未结算'),
      profitText: showProfit ? formatSignedMoney(profit) : '',
    };
  }

  function buildFollowExecutionModel(rows, options) {
    const opts = options || {};
    const source = Array.isArray(rows) ? rows : [];
    const seen = new Set();
    const items = [];

    for (const row of source) {
      if (!row || typeof row !== 'object') continue;
      const ticket = followTicketId(row);
      const ok = !!row.ok;
      const key = [
        compactText(row.member_id || row.member),
        ticket,
        String(ok),
        compactText(row.ts_ms || row.ts),
        compactText(row.attempt),
        compactText(row.split_index),
        compactText(row.message),
      ].join('|');
      if (seen.has(key)) continue;
      seen.add(key);

      const splitIndex = Number(row.split_index || 0);
      const splitCount = Number(row.split_count || 0);
      const splitText = splitCount > 1 && splitIndex > 0 ? `${splitIndex}/${splitCount}` : '';
      const attemptText = row.attempt ? `第 ${row.attempt} 次` : '';
      const memberText = firstText(row.member, row.member_id, '未知会员');
      items.push({
        ok,
        status: ok ? 'success' : 'failed',
        statusLabel: ok ? '成功' : '失败',
        member: memberText,
        stakeText: formatFollowStake(row.stake),
        ticketText: ticket || '—',
        reason: ok ? '已成功出票' : followFailureReason(row),
        attemptText,
        splitText,
        ts: toFiniteNumber(row.ts_ms || row.ts),
      });
    }

    items.sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
    // Execution logs contain one row per attempt.  The report summary must
    // count independent split orders, not every retry/error row.
    const splitTotal = source.reduce((max, row) => {
      const count = Number(row && (row.split_count || row.split_total) || 0);
      const index = Number(row && row.split_index || 0);
      return Math.max(max, Number.isFinite(count) ? count : 0, Number.isFinite(index) ? index : 0);
    }, 0);
    const splitStates = new Map();
    const legacyStates = [];
    for (const row of source) {
      if (!row || typeof row !== 'object') continue;
      const index = Number(row.split_index || 0);
      const ticket = followTicketId(row);
      if (splitTotal > 1 && Number.isFinite(index) && index > 0) {
        const previous = splitStates.get(index);
        // A successful ticket is terminal. Otherwise retain the latest
        // attempt so the card can still explain the current failure.
        if (!previous || (!previous.ok && !!row.ok) || (!previous.ok && !row.ok && Number(row.ts_ms || row.ts || 0) >= Number(previous.ts_ms || previous.ts || 0))) {
          splitStates.set(index, row);
        }
      } else if (ticket) {
        const previous = legacyStates.find((item) => followTicketId(item) === ticket);
        if (!previous) legacyStates.push(row);
      } else if (!legacyStates.length || row.ok || !legacyStates[0].ok) {
        legacyStates[0] = row;
      }
    }
    const groupedStates = splitTotal > 1
      ? Array.from({ length: splitTotal }, (_, offset) => splitStates.get(offset + 1) || null)
      : legacyStates;
    const okCount = groupedStates.filter((row) => row && row.ok).length;
    const failCount = groupedStates.filter((row) => row && !row.ok).length;
    const pendingCount = groupedStates.filter((row) => !row).length;

    if (!items.length) {
      const engineStopped = !!opts.engineStopped;
      return {
        rows: [],
        ok: 0,
        fail: 0,
        pending: 1,
        summaryText: engineStopped ? '跟单引擎未运行' : '待处理',
        emptyText: engineStopped
          ? '该订单尚未产生跟单记录，当前跟单引擎未运行。'
          : '该订单尚未产生跟单执行记录，等待引擎处理或结果回写。',
      };
    }

    return {
      rows: items,
      ok: okCount,
      fail: failCount,
      pending: pendingCount,
      summaryText: `成功 ${okCount} · 失败 ${failCount}${pendingCount > 0 ? ` · 待处理 ${pendingCount}` : ''}`,
      emptyText: '',
    };
  }

  return {
    buildFollowExecutionModel,
    buildReportCardModel,
    formatCompactMoney,
    formatSignedMoney,
    parseBetDisplay,
    settlementTone,
  };
});
