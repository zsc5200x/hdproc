import {
  Activity,
  Bell,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Columns3,
  Gauge,
  ListFilter,
  Menu,
  MessageCircleMore,
  MoreHorizontal,
  RadioTower,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Target,
  Users,
  Wifi,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ActivityChart } from "./components/ActivityChart";
import { MetricCard } from "./components/MetricCard";
import { OrderDrawer } from "./components/OrderDrawer";
import { OrderTable } from "./components/OrderTable";
import { Sidebar, type PageId } from "./components/Sidebar";
import { getDashboardSnapshot, setPushEngine } from "./data/dashboard";
import type { DashboardSnapshot, OrderStatus, PushOrder } from "./types";

const pageTitles: Record<PageId, { eyebrow: string; title: string; subtitle: string }> = {
  overview: { eyebrow: "OPERATIONS", title: "推送总览", subtitle: "实时订单捕获与 Telegram 投递状态" },
  orders: { eyebrow: "ORDER STREAM", title: "实时订单", subtitle: "查看、筛选并追踪每一笔推送订单" },
  targets: { eyebrow: "WATCH TARGETS", title: "监听目标", subtitle: "AG 账号连接状态与订单捕获统计" },
  channels: { eyebrow: "TELEGRAM", title: "TG 频道", subtitle: "投递频道连接与送达数据" },
  logs: { eyebrow: "SYSTEM TRACE", title: "运行日志", subtitle: "推送链路中的关键事件和异常" },
  settings: { eyebrow: "PREFERENCES", title: "系统设置", subtitle: "推送策略与终端显示偏好" },
};

const statusFilters: Array<{ id: "all" | OrderStatus; label: string }> = [
  { id: "all", label: "全部" }, { id: "sent", label: "已推送" }, { id: "pending", label: "排队中" }, { id: "failed", label: "失败" },
];

function PanelHeader({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: React.ReactNode }) {
  return <header className="panel-header"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div>{action}</header>;
}

function LiveBand({ running, onToggle }: { running: boolean; onToggle: () => void }) {
  return (
    <section className={`live-band ${running ? "" : "live-band--paused"}`}>
      <div className="pitch-lines" aria-hidden="true"><i /><span /><b /></div>
      <div className="live-band__content">
        <div className="live-band__state"><span className="pulse"><i /></span><div><span className="eyebrow">LIVE PUSH ENGINE</span><h2>{running ? "推送引擎运行中" : "推送已暂停"}</h2><p>{running ? "4 个监听目标正在同步，TG 链路延迟 0.8 秒" : "新订单仍会记录，恢复后继续投递"}</p></div></div>
        <div className="live-band__stats"><div><span>队列</span><strong>1</strong></div><div><span>平均延迟</span><strong>0.8s</strong></div><div><span>最近推送</span><strong>4 秒前</strong></div></div>
        <button type="button" className={`engine-switch ${running ? "is-on" : ""}`} role="switch" aria-label="推送引擎" aria-checked={running} onClick={onToggle}><span><Zap size={14} /></span>{running ? "运行中" : "已暂停"}</button>
      </div>
    </section>
  );
}

function ChannelHealth({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
    <section className="channel-panel tool-panel">
      <PanelHeader eyebrow="DELIVERY" title="频道健康度" action={<button className="icon-button" type="button" aria-label="频道更多操作"><MoreHorizontal size={17} /></button>} />
      <div className="channel-score"><div className="score-ring"><strong>99</strong><span>健康度</span></div><div><strong>Telegram API</strong><span><i /> 服务正常</span><small>最近检测 14:08:34</small></div></div>
      <div className="channel-list">
        {snapshot.channels.map((channel) => <div key={channel.id}><span className="channel-logo"><Send size={15} /></span><span><strong>{channel.name}</strong><small>{channel.audience}</small></span><span className="delivery-count"><strong>{channel.deliveries}</strong><small>今日送达</small></span></div>)}
      </div>
    </section>
  );
}

interface OrderSectionProps {
  orders: PushOrder[];
  status: "all" | OrderStatus;
  onStatus: (value: "all" | OrderStatus) => void;
  density: "compact" | "comfortable";
  onDensity: () => void;
  onSelect: (order: PushOrder) => void;
  expanded?: boolean;
}

function OrderSection({ orders, status, onStatus, density, onDensity, onSelect, expanded }: OrderSectionProps) {
  return (
    <section className={`orders-panel tool-panel ${expanded ? "orders-panel--expanded" : ""}`}>
      <PanelHeader eyebrow="ORDER STREAM" title="实时订单流" action={<div className="panel-actions"><div className="segmented" aria-label="订单状态筛选">{statusFilters.map((item) => <button type="button" key={item.id} className={status === item.id ? "active" : ""} onClick={() => onStatus(item.id)}>{item.label}</button>)}</div><button type="button" className="icon-button" title="切换行距" aria-label="切换订单行距" onClick={onDensity}><Columns3 size={17} /></button><button type="button" className="icon-button" aria-label="订单筛选"><ListFilter size={17} /></button></div>} />
      <OrderTable orders={orders} density={density} onSelect={onSelect} />
    </section>
  );
}

function Overview({ snapshot, orders, status, setStatus, density, setDensity, onSelect, running, onToggle }: {
  snapshot: DashboardSnapshot; orders: PushOrder[]; status: "all" | OrderStatus; setStatus: (value: "all" | OrderStatus) => void; density: "compact" | "comfortable"; setDensity: () => void; onSelect: (order: PushOrder) => void; running: boolean; onToggle: () => void;
}) {
  const { metrics } = snapshot;
  return <>
    <LiveBand running={running} onToggle={onToggle} />
    <section className="metrics-grid" aria-label="今日推送指标">
      <MetricCard label="今日订单" value={String(metrics.ordersToday)} detail="较昨日 +12.4%" tone="blue" icon={Activity} />
      <MetricCard label="已推送" value={String(metrics.pushedToday)} detail="3 条待处理" tone="cyan" icon={Send} />
      <MetricCard label="推送成功率" value={`${metrics.successRate}%`} detail="目标 98%" tone="green" icon={ShieldCheck} progress={metrics.successRate} />
      <MetricCard label="推送失败" value={String(metrics.failedToday)} detail="2 条正在重试" tone="red" icon={CircleAlert} />
      <MetricCard label="在线目标" value={`${metrics.targetsOnline}/4`} detail="全部监听中" tone="violet" icon={Target} />
      <MetricCard label="TG 频道" value={`${metrics.channelsOnline}/2`} detail="链路稳定" tone="amber" icon={MessageCircleMore} />
    </section>
    <div className="analysis-grid">
      <section className="trend-panel tool-panel"><PanelHeader eyebrow="LAST 7 HOURS" title="订单推送趋势" action={<div className="compact-select"><span>今日</span><ChevronDown size={14} /></div>} /><ActivityChart data={snapshot.trend} /><footer className="chart-legend"><span><i className="blue" /> 订单数量</span><span><i className="green" /> 平均成功率 {Math.round(snapshot.trend.reduce((sum, item) => sum + item.success, 0) / snapshot.trend.length)}%</span></footer></section>
      <ChannelHealth snapshot={snapshot} />
    </div>
    <OrderSection orders={orders} status={status} onStatus={setStatus} density={density} onDensity={setDensity} onSelect={onSelect} />
  </>;
}

function TargetsPage({ snapshot }: { snapshot: DashboardSnapshot }) {
  return <section className="data-page tool-panel"><PanelHeader eyebrow="4 ACTIVE SOURCES" title="监听账号状态" action={<button type="button" className="primary-button"><Target size={16} />新增目标</button>} /><div className="entity-list">{snapshot.targets.map((target) => <article key={target.id}><span className={`entity-icon ${target.state}`}><Users size={18} /></span><span className="entity-title"><strong>{target.name}</strong><small>{target.alias}</small></span><span><small>今日订单</small><strong>{target.orders}</strong></span><span><small>最近心跳</small><strong>{target.lastSeen}</strong></span><span className={`connection ${target.state}`}><i />{target.state === "online" ? "监听中" : "延迟偏高"}</span><button type="button" className="icon-button" aria-label={`${target.name}设置`}><Settings2 size={16} /></button></article>)}</div></section>;
}

function ChannelsPage({ snapshot }: { snapshot: DashboardSnapshot }) {
  return <div className="channels-page">{snapshot.channels.map((channel, index) => <article className="channel-detail" key={channel.id}><header><span className="channel-logo large"><Send size={20} /></span><span><small>TELEGRAM CHANNEL</small><strong>{channel.name}</strong></span><span className="connection online"><i />已连接</span></header><div className="channel-detail__metrics"><div><small>今日投递</small><strong>{channel.deliveries}</strong></div><div><small>订阅用户</small><strong>{channel.audience.split(" ")[0]}</strong></div><div><small>成功率</small><strong>{index === 0 ? "99.2%" : "98.1%"}</strong></div></div><footer><span>ID {channel.id}</span><button type="button" className="secondary-button"><Settings2 size={15} />配置频道</button></footer></article>)}</div>;
}

function LogsPage() {
  const logs = [
    ["14:08:36.912", "info", "Telegram", "订单 HD-0725-0186 已送达频道 HD 实时单"],
    ["14:08:32.284", "info", "Formatter", "已生成纯文本推送消息，无附加按钮"],
    ["14:03:55.108", "warn", "Retry", "订单 HD-0725-0184 投递超时，将在 15 秒后重试"],
    ["14:03:51.126", "info", "Watcher", "捕获目标 2fa07 新订单，进入投递队列"],
    ["13:57:00.004", "info", "Health", "4 个监听目标、2 个 TG 频道状态正常"],
  ];
  return <section className="log-panel tool-panel"><PanelHeader eyebrow="LIVE TRACE" title="运行事件" action={<button type="button" className="secondary-button"><RefreshCw size={15} />刷新日志</button>} /><div className="log-list">{logs.map(([time, level, source, text]) => <div key={time}><time>{time}</time><span className={`log-level ${level}`}>{level.toUpperCase()}</span><strong>{source}</strong><p>{text}</p></div>)}</div></section>;
}

function SettingsPage() {
  const [states, setStates] = useState({ retry: true, sound: false, launch: true });
  const toggle = (key: keyof typeof states) => setStates((current) => ({ ...current, [key]: !current[key] }));
  return <div className="settings-page"><section className="settings-section"><PanelHeader eyebrow="DELIVERY" title="推送策略" /><div className="setting-row"><span><strong>失败自动重试</strong><small>Telegram 请求异常时最多重试 3 次</small></span><button type="button" role="switch" aria-label="失败自动重试" aria-checked={states.retry} className={`small-switch ${states.retry ? "is-on" : ""}`} onClick={() => toggle("retry")}><i /></button></div><div className="setting-row"><span><strong>新订单提示音</strong><small>捕获订单时播放本地提示</small></span><button type="button" role="switch" aria-label="新订单提示音" aria-checked={states.sound} className={`small-switch ${states.sound ? "is-on" : ""}`} onClick={() => toggle("sound")}><i /></button></div></section><section className="settings-section"><PanelHeader eyebrow="SYSTEM" title="终端行为" /><div className="setting-row"><span><strong>开机自动运行</strong><small>登录 macOS 后自动启动推送服务</small></span><button type="button" role="switch" aria-label="开机自动运行" aria-checked={states.launch} className={`small-switch ${states.launch ? "is-on" : ""}`} onClick={() => toggle("launch")}><i /></button></div></section></div>;
}

export function App() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [activePage, setActivePage] = useState<PageId>("overview");
  const [status, setStatus] = useState<"all" | OrderStatus>("all");
  const [query, setQuery] = useState("");
  const [density, setDensity] = useState<"compact" | "comfortable">("compact");
  const [selectedOrder, setSelectedOrder] = useState<PushOrder | null>(null);
  const [running, setRunning] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [lastRefresh, setLastRefresh] = useState("刚刚更新");

  useEffect(() => { getDashboardSnapshot().then(setSnapshot); }, []);

  const visibleOrders = useMemo(() => {
    if (!snapshot) return [];
    const needle = query.trim().toLowerCase();
    return snapshot.orders.filter((item) => (status === "all" || item.status === status) && (!needle || [item.id, item.league, item.match, item.target].some((value) => value.toLowerCase().includes(needle))));
  }, [snapshot, status, query]);

  const toggleEngine = async () => {
    const next = !running;
    setRunning(await setPushEngine(next));
  };

  const refresh = async () => {
    setSnapshot(await getDashboardSnapshot());
    setLastRefresh("刚刚更新");
  };

  if (!snapshot) return <main className="loading-screen"><span className="loading-mark"><Zap size={20} /></span><strong>正在连接推送终端</strong></main>;

  const page = pageTitles[activePage];
  return (
    <div className="app-shell">
      <Sidebar active={activePage} onChange={setActivePage} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      {sidebarOpen && <button type="button" className="mobile-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
      <main className="workspace">
        <header className="topbar">
          <button type="button" className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={18} /></button>
          <label className="global-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索订单、球队或目标账号" aria-label="全局搜索" /><kbd>⌘ K</kbd></label>
          <div className="topbar-actions"><span className="sync-state"><Wifi size={14} /><span>IPC 已连接</span></span><button type="button" className="icon-button" aria-label="刷新数据" title="刷新数据" onClick={refresh}><RefreshCw size={16} /></button><button type="button" className="icon-button has-notice" aria-label="通知"><Bell size={16} /><i /></button><button type="button" className="user-menu"><span>HD</span><div><strong>操作员</strong><small>管理员</small></div><ChevronDown size={14} /></button></div>
        </header>
        <div className="workspace-scroll">
          <header className="page-header"><div><span className="eyebrow">{page.eyebrow}</span><h1>{page.title}</h1><p>{page.subtitle}</p></div><div className="page-meta"><span><Clock3 size={14} />{lastRefresh}</span><button type="button" className="secondary-button" aria-label="运行报告"><Gauge size={15} /><span>运行报告</span></button></div></header>
          {activePage === "overview" && <Overview snapshot={snapshot} orders={visibleOrders} status={status} setStatus={setStatus} density={density} setDensity={() => setDensity((value) => value === "compact" ? "comfortable" : "compact")} onSelect={setSelectedOrder} running={running} onToggle={toggleEngine} />}
          {activePage === "orders" && <OrderSection expanded orders={visibleOrders} status={status} onStatus={setStatus} density={density} onDensity={() => setDensity((value) => value === "compact" ? "comfortable" : "compact")} onSelect={setSelectedOrder} />}
          {activePage === "targets" && <TargetsPage snapshot={snapshot} />}
          {activePage === "channels" && <ChannelsPage snapshot={snapshot} />}
          {activePage === "logs" && <LogsPage />}
          {activePage === "settings" && <SettingsPage />}
          <footer className="workspace-footer"><span><i /> 系统运行正常</span><span>数据时间 {new Date(snapshot.generatedAt).toLocaleTimeString("zh-CN", { hour12: false })}</span></footer>
        </div>
      </main>
      {selectedOrder && <OrderDrawer order={selectedOrder} onClose={() => setSelectedOrder(null)} />}
    </div>
  );
}
