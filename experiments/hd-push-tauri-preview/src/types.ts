export type OrderStatus = "sent" | "failed" | "pending";

export interface DashboardMetrics {
  ordersToday: number;
  pushedToday: number;
  successRate: number;
  failedToday: number;
  targetsOnline: number;
  channelsOnline: number;
}

export interface TrendPoint {
  label: string;
  orders: number;
  success: number;
}

export interface PushOrder {
  id: string;
  time: string;
  league: string;
  match: string;
  phase: "滚球" | "早盘";
  market: string;
  period: string;
  selection: string;
  amount: number;
  status: OrderStatus;
  target: string;
  channel: string;
  latency: string;
  message: string;
}

export interface TargetAccount {
  id: string;
  name: string;
  alias: string;
  state: "online" | "warning";
  orders: number;
  lastSeen: string;
}

export interface TelegramChannel {
  id: string;
  name: string;
  audience: string;
  state: "connected" | "degraded";
  deliveries: number;
}

export interface DashboardSnapshot {
  generatedAt: string;
  metrics: DashboardMetrics;
  trend: TrendPoint[];
  orders: PushOrder[];
  targets: TargetAccount[];
  channels: TelegramChannel[];
}
