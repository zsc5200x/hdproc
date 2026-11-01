/** Only source timestamps describe freshness. Response generatedAt is intentionally ignored. */
export function describeSource(name, source) {
  if (!source || typeof source !== "object") {
    return { text: name + " · 尚未取得来源状态", level: "unknown", error: "" };
  }
  const labels = { live: "实时", delayed: "延迟", stale: "已过期", "no-data": "无数据" };
  const status = labels[source.status] || String(source.status || "状态未知");
  const date = source.dataUpdatedAt ? new Date(source.dataUpdatedAt) : null;
  const validDate = date && Number.isFinite(date.getTime());
  const stamp = validDate ? date.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }) + " 北京时间" : "时间未知";
  return {
    text: name + " · " + status + (source.cached ? " · 缓存" : "") + " · 数据 " + stamp,
    level: source.error ? "error" : source.cached || ["delayed", "stale"].includes(source.status) ? "warning" : validDate ? "normal" : "unknown",
    error: String(source.error || ""),
  };
}
