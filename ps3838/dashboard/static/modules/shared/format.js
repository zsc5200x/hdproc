export function iconArrow(direction) {
  if (direction === "up") return "↑";
  if (direction === "down") return "↓";
  return "•";
}

export function parseDate(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function leagueDisplayName(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  // PS3838 uses "国家 - 联赛" as the display separator. Keep hyphens
  // inside competition names such as "U-20" or "I-League".
  return text.replace(/^(.{2,}?)\s*[-–—]\s*(.+)$/, "$1$2");
}

export function formatClock(value) {
  const parsed = parseDate(value);
  if (!parsed) return "--:--:--";
  return parsed.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatShortDate(value) {
  const parsed = parseDate(value);
  if (!parsed) return "--";
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function formatKickoff(value) {
  if (!value) return "开赛时间未知";
  const parsed = parseDate(value.replace(" ", "T"));
  if (!parsed) return value;
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "无数据";
  if (seconds < 1) return "<1 秒";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}

export function formatPrice(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }
  return Number(value).toFixed(digits);
}

export function numericValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function rowHongKong(row) {
  const hongKong = numericValue(row?.hongKong);
  if (hongKong !== null) return hongKong;
  const decimal = numericValue(row?.decimal);
  return decimal === null ? null : decimal - 1;
}

export function historyHongKong(point) {
  const hongKong = numericValue(point?.hongKong);
  if (hongKong !== null) return hongKong;
  let decimal = numericValue(point?.decimal);
  if (decimal === null) decimal = numericValue(point?.v);
  return decimal === null ? null : decimal - 1;
}

export function decimalToHongKong(value) {
  const decimal = numericValue(value);
  return decimal === null ? null : decimal - 1;
}

export function formatDelta(value) {
  if (value === null || value === undefined) return "";
  const number = Number(value);
  if (number > 0) return `+${number.toFixed(3)}`;
  return number.toFixed(3);
}

export function matchPeriodLabel(value) {
  return {
    "1H": "上半场",
    "2H": "下半场",
    HT: "中场",
    FT: "完场",
    ET: "加时",
    PEN: "点球",
  }[String(value || "").toUpperCase()] || String(value || "");
}

export function matchClockLabel(event) {
  const parts = [];
  if (event?.matchMinute) parts.push(String(event.matchMinute));
  const period = matchPeriodLabel(event?.matchPeriod);
  if (period) parts.push(period);
  return parts.join(" · ");
}

export function quoteAgeInfo(value, referenceValue) {
  const captured = parseDate(value);
  const reference = parseDate(referenceValue) || new Date();
  if (!captured) return { text: "时间未知", level: "unknown", seconds: null };
  const seconds = Math.max(0, Math.round((reference.getTime() - captured.getTime()) / 1000));
  if (seconds <= 10) return { text: "刚刚", level: "live", seconds };
  if (seconds < 60) return { text: `${seconds} 秒前`, level: "live", seconds };
  if (seconds < 3600) {
    return {
      text: `${Math.floor(seconds / 60)} 分前`,
      level: seconds < 300 ? "recent" : "aged",
      seconds,
    };
  }
  return {
    text: `${Math.floor(seconds / 3600)} 小时前`,
    level: "aged",
    seconds,
  };
}

export function handicapSideLine(row) {
  const line = Number(row?.line);
  if (!Number.isFinite(line)) return null;
  return String(row.option) === "0" ? line : -line;
}

export function countMarketGroups(rows) {
  let count = rows.some((row) => String(row.market) === "1") ? 1 : 0;
  const handicapLines = new Set();
  const totalLines = new Set();
  for (const row of rows) {
    if (String(row.market) === "2") {
      const line = handicapSideLine(row);
      if (line !== null) handicapLines.add(line.toFixed(4));
    }
    if (String(row.market) === "3") {
      const line = Number(row.line);
      if (Number.isFinite(line)) totalLines.add(line.toFixed(4));
    }
  }
  return count + handicapLines.size + totalLines.size;
}

export function qualityLabel(status) {
  return {
    complete: "元数据完整",
    partial: "元数据部分缺失",
    missing: "元数据缺失",
  }[status] || "元数据未知";
}

export function marketLabel(code) {
  return {
    1: "胜平负",
    2: "让球",
    3: "大小球",
  }[code] || `盘口 ${code || "—"}`;
}

export function isFullTimeMarketRow(row) {
  return String(row.stage) === "0" && ["1", "2", "3"].includes(String(row.market));
}

export function stageLabel(code) {
  return {
    0: "全场",
    1: "上半场",
    2: "下半场",
  }[code] || `阶段 ${code || "—"}`;
}

export function formatLineNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  const rounded = number.toFixed(2).replace(/\.?0+$/, "");
  return rounded === "-0" ? "0" : rounded;
}

export function formatQuarterLine(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return formatLineNumber(value);
  if (Math.abs(number) < 0.000001) return "0";
  const magnitude = Math.abs(number);
  const quarterSteps = magnitude * 4;
  const roundedSteps = Math.round(quarterSteps);
  const sign = number < 0 ? "-" : "";
  if (Math.abs(quarterSteps - roundedSteps) < 0.000001 && roundedSteps % 2 === 1) {
    return `${sign}${formatLineNumber(magnitude - 0.25)}/${formatLineNumber(magnitude + 0.25)}`;
  }
  return `${sign}${formatLineNumber(magnitude)}`;
}

// Crown renders quarter-goal lines as split halves ("0/0.5" for 0.25,
// "0.5/1" for 0.75) and keeps an explicit direction sign. Use the same
// notation for Pinnacle rows so both columns read identically.
export function formatHandicapLine(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return formatLineNumber(value);
  if (Math.abs(number) < 0.000001) return "0";
  return `${number > 0 ? "+" : ""}${formatQuarterLine(number)}`;
}

export function formatTotalLine(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return formatLineNumber(value);
  const quarterSteps = number * 4;
  const roundedSteps = Math.round(quarterSteps);
  if (Math.abs(quarterSteps - roundedSteps) < 0.000001 && Math.abs(roundedSteps) % 2 === 1) {
    return `${formatLineNumber(number - 0.25)}/${formatLineNumber(number + 0.25)}`;
  }
  return formatLineNumber(number);
}

export function teamNames(eventName) {
  const parts = String(eventName || "").split(/\s+VS\s+/i);
  return [parts[0] || "主队", parts[1] || "客队"];
}

export function selectionLabel(row, eventName) {
  const [home, away] = teamNames(eventName);
  if (String(row.market) === "1") {
    // Pinnacle's 1X2 option codes are 0=home, 1=away, 2=draw.
    return {
      primary: { 0: "主胜", 1: "客胜", 2: "和局" }[String(row.option)] || `选项 ${row.option}`,
      detail: { 0: home, 1: away, 2: "双方打平" }[String(row.option)] || "独赢",
      line: "—",
    };
  }
  if (String(row.market) === "2") {
    const isHome = String(row.option) === "0";
    const lineNumber = Number(row.line);
    const sideLine = isHome ? lineNumber : -lineNumber;
    const line = formatHandicapLine(sideLine);
    const role = !Number.isFinite(sideLine) || Math.abs(sideLine) < 0.000001
      ? `${isHome ? "主队" : "客队"}平手`
      : sideLine < 0
        ? `${isHome ? "主队" : "客队"}让球`
        : `${isHome ? "主队" : "客队"}受让`;
    return {
      primary: `${isHome ? home : away} ${line}`,
      detail: role,
      line,
    };
  }
  if (String(row.market) === "3") {
    const line = formatTotalLine(row.line);
    const isOver = String(row.option) === "3";
    return {
      primary: `${isOver ? "大" : "小"}${line}`,
      detail: "总进球",
      line,
    };
  }
  return {
    primary: `选项 ${row.option || "—"}`,
    detail: "未知玩法",
    line: formatLineNumber(row.line),
  };
}

export function historyRowLabel(row, eventName) {
  const label = selectionLabel(row, eventName);
  return `${marketLabel(row.market)} · ${stageLabel(row.stage)} · ${label.primary}`;
}

export function formatKickoffParts(value) {
  const parsed = value ? parseDate(String(value).replace(" ", "T")) : null;
  if (!parsed) return { time: "--:--", date: "日期未知" };
  return {
    time: parsed.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }),
    date: `${String(parsed.getMonth() + 1).padStart(2, "0")}/${String(parsed.getDate()).padStart(2, "0")}`,
  };
}

export function periodLabel(code) {
  return {
    "1H": "上半场",
    "2H": "下半场",
    HT: "中场",
    ET: "加时",
    PEN: "点球",
  }[String(code || "").toUpperCase()] || "进行中";
}
