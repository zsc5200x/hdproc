import { invoke } from "@tauri-apps/api/core";
import type { DashboardSnapshot, PushOrder } from "../types";

const messageFor = (order: Pick<PushOrder, "league" | "match" | "phase" | "market" | "period" | "selection" | "amount">) =>
  [
    "🤣新注单提醒🤣",
    "⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️ ⚽️",
    `🤣${order.league}`,
    `🤣${order.match}`,
    "",
    `📍 状态：${order.phase}`,
    `玩法：${order.market}`,
    `时段：${order.period}`,
    `选择：${order.selection}`,
    "",
    `💰 金额：${order.amount}`,
  ].join("\n");

const order = (value: Omit<PushOrder, "message">): PushOrder => ({
  ...value,
  message: messageFor(value),
});

export const fallbackSnapshot: DashboardSnapshot = {
  generatedAt: "2026-07-25T14:08:36+08:00",
  metrics: {
    ordersToday: 186,
    pushedToday: 183,
    successRate: 98.7,
    failedToday: 3,
    targetsOnline: 4,
    channelsOnline: 2,
  },
  trend: [
    { label: "08:00", orders: 12, success: 100 },
    { label: "09:00", orders: 23, success: 100 },
    { label: "10:00", orders: 18, success: 94 },
    { label: "11:00", orders: 31, success: 97 },
    { label: "12:00", orders: 27, success: 100 },
    { label: "13:00", orders: 36, success: 98 },
    { label: "14:00", orders: 39, success: 99 },
  ],
  orders: [
    order({ id: "HD-0725-0186", time: "14:08:32", league: "英格兰超级联赛", match: "阿森纳 vs 切尔西", phase: "滚球", market: "让球", period: "全场", selection: "阿森纳 -0.5 @0.95", amount: 100, status: "sent", target: "主账号 · 8c91a", channel: "HD 实时单", latency: "0.7s" }),
    order({ id: "HD-0725-0185", time: "14:06:18", league: "西班牙甲级联赛", match: "巴塞罗那 vs 比利亚雷亚尔", phase: "早盘", market: "进球大小", period: "全场", selection: "大 2.5/3 @0.88", amount: 300, status: "pending", target: "主账号 · 8c91a", channel: "HD 实时单", latency: "排队中" }),
    order({ id: "HD-0725-0184", time: "14:03:51", league: "法国甲级联赛", match: "巴黎圣日耳曼 vs 里昂", phase: "滚球", market: "独赢", period: "下半场", selection: "巴黎圣日耳曼 @0.76", amount: 200, status: "failed", target: "观察账号 · 2fa07", channel: "VIP 精选单", latency: "重试 2/3" }),
    order({ id: "HD-0725-0183", time: "13:58:04", league: "德国甲级联赛", match: "拜仁慕尼黑 vs 多特蒙德", phase: "滚球", market: "进球大小", period: "上半场", selection: "小 1.5 @0.91", amount: 500, status: "sent", target: "主账号 · 8c91a", channel: "HD 实时单", latency: "0.9s" }),
    order({ id: "HD-0725-0182", time: "13:52:27", league: "意大利甲级联赛", match: "国际米兰 vs AC米兰", phase: "早盘", market: "让球", period: "全场", selection: "国际米兰 -0/0.5 @0.86", amount: 150, status: "sent", target: "副账号 · b783d", channel: "VIP 精选单", latency: "0.6s" }),
    order({ id: "HD-0725-0181", time: "13:47:12", league: "日本职业联赛", match: "川崎前锋 vs 横滨水手", phase: "滚球", market: "进球大小", period: "全场", selection: "大 3 @1.02", amount: 260, status: "sent", target: "副账号 · b783d", channel: "HD 实时单", latency: "0.8s" }),
  ],
  targets: [
    { id: "8c91a", name: "主监听账号", alias: "AG · 8c91a", state: "online", orders: 96, lastSeen: "刚刚" },
    { id: "b783d", name: "副监听账号", alias: "AG · b783d", state: "online", orders: 54, lastSeen: "3 秒前" },
    { id: "2fa07", name: "观察账号", alias: "AG · 2fa07", state: "warning", orders: 21, lastSeen: "18 秒前" },
    { id: "96bd1", name: "夜盘账号", alias: "AG · 96bd1", state: "online", orders: 15, lastSeen: "6 秒前" },
  ],
  channels: [
    { id: "-1002186001", name: "HD 实时单", audience: "1,286 位订阅者", state: "connected", deliveries: 132 },
    { id: "-1002186002", name: "VIP 精选单", audience: "368 位订阅者", state: "connected", deliveries: 51 },
  ],
};

export const isTauriRuntime = () => "__TAURI_INTERNALS__" in window;

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  if (isTauriRuntime()) {
    return invoke<DashboardSnapshot>("get_dashboard_snapshot");
  }
  return Promise.resolve(fallbackSnapshot);
}

export async function setPushEngine(enabled: boolean): Promise<boolean> {
  if (isTauriRuntime()) {
    return invoke<boolean>("set_push_engine", { enabled });
  }
  return Promise.resolve(enabled);
}
