(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.HDOrderList = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function text(value, fallback = "—") {
    const result = String(value == null ? "" : value).trim();
    return result || fallback;
  }

  function money(value) {
    const amount = Number(value) || 0;
    const prefix = amount < 0 ? "-¥" : "¥";
    const absolute = Math.abs(amount);
    return absolute >= 1000 ? `${prefix}${(absolute / 1000).toFixed(1)}k` : `${prefix}${Math.floor(absolute)}`;
  }

  function compactOdds(value) {
    const raw = text(value, "").replace(/^@\s*/, "").trim();
    if (!raw || raw === "—" || raw === "--") return "";
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw)) return raw;
    const numeric = Number(raw);
    return Number.isFinite(numeric) ? String(numeric) : raw;
  }

  function formatMarketText(selectionText, oddsText, fallback) {
    const selection = text(selectionText, "")
      .replace(/(^|\s)(大|小)\s+(?=[+-]?(?:\d|\.\d))/g, "$1$2")
      .trim();
    const embeddedOdds = selection.match(/\s*@\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))/);
    if (embeddedOdds) {
      return selection.replace(embeddedOdds[0], `@${compactOdds(embeddedOdds[1])}`);
    }

    const odds = compactOdds(oddsText);
    if (selection) return odds ? `${selection}@${odds}` : selection;
    return text(fallback);
  }

  function followStatus(summary, searchOnly) {
    if (searchOnly) return "比赛查询";
    const source = summary || {};
    const ok = Number(source.ok || 0);
    const fail = Number(source.fail || 0);
    if (ok && fail) return `成功 ${ok} 单 · 失败 ${fail} 单`;
    if (ok) return `成功 ${ok} 单`;
    if (fail) return `失败 ${fail} 单`;
    return source.engineStopped ? "引擎未运行" : "待处理";
  }

  function followTone(summary, searchOnly) {
    if (searchOnly) return "idle";
    const source = summary || {};
    const ok = Number(source.ok || 0);
    const fail = Number(source.fail || 0);
    if (ok && fail) return "warn";
    if (ok) return "ok";
    if (fail) return "fail";
    return "idle";
  }

  function settlementStatus(settlement, searchOnly) {
    if (searchOnly) return { text: "—", tone: "pending" };
    const source = settlement || {};
    const status = text(source.status, "pending").toLowerCase();
    const label = text(source.status_label, "未结算");
    const profit = Number(source.profit_sum || 0);
    if (status === "win" || status === "lose") {
      return { text: `${label} · ${profit >= 0 ? "+" : ""}${money(profit)}`, tone: status };
    }
    return { text: label, tone: "pending" };
  }

  function buildRealtimeOrderListRow(input) {
    const source = input || {};
    const model = source.model || {};
    const searchOnly = !!source.searchOnly;
    if (model.readOnly) {
      return {
        league: text(model.league), orderKind: text(model.orderKind),
        homeTeam: text(model.homeTeam), awayTeam: text(model.awayTeam), score: text(model.score),
        sourceLabel: text(model.sourceLabel), statusLine: text(model.statusLine),
        marketText: formatMarketText(model.selectionText, model.oddsText, model.marketText),
        marketCaption: text(model.marketText), betAmountText: text(model.stakeStr),
        followAmountText: "—", followStatusText: model.statusText === "接口未提供" ? "只读监控" : `只读 · ${text(model.statusText)}`, followTone: "idle",
        settlementText: text(model.settlementText, "未提供"), settlementTone: "pending",
        isSearchOnly: false, readOnly: true,
      };
    }
    const settlement = settlementStatus(source.settlement, searchOnly);
    const marketText = searchOnly
      ? "—"
      : formatMarketText(model.selectionText, model.oddsText, model.marketText);

    return {
      league: text(model.league),
      orderKind: text(model.orderKind || model.modeLabel, "滚球"),
      homeTeam: text(model.homeTeam, "主队"),
      awayTeam: text(model.awayTeam, "客队"),
      score: text(model.score, "0 : 0"),
      matchText: `${text(model.homeTeam, "主队")}|${text(model.score, "0 : 0")}|${text(model.awayTeam, "客队")}`,
      statusLine: text(model.statusLine),
      marketText,
      marketCaption: searchOnly ? "" : text(model.marketText),
      betAmountText: searchOnly ? "—" : text(model.stakeStr, "¥0"),
      followAmountText: searchOnly ? "—" : money(source.followStake),
      followStatusText: followStatus(source.followSummary, searchOnly),
      followTone: followTone(source.followSummary, searchOnly),
      settlementText: settlement.text,
      settlementTone: settlement.tone,
      isSearchOnly: searchOnly,
    };
  }

  function shouldReplaceRealtimeOrderList(previousSignature, nextSignature, hasChildren) {
    return !hasChildren || String(previousSignature || "") !== String(nextSignature || "");
  }

  return { buildRealtimeOrderListRow, shouldReplaceRealtimeOrderList };
});
