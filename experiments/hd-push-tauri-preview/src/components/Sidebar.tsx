import {
  Activity,
  Bot,
  ChevronLeft,
  FileClock,
  LayoutDashboard,
  RadioTower,
  Settings,
  Target,
} from "lucide-react";
import { BrandMark } from "./BrandMark";

export type PageId = "overview" | "orders" | "targets" | "channels" | "logs" | "settings";

const navigation = [
  { id: "overview", label: "推送总览", icon: LayoutDashboard },
  { id: "orders", label: "实时订单", icon: Activity, badge: "186" },
  { id: "targets", label: "监听目标", icon: Target, badge: "4" },
  { id: "channels", label: "TG 频道", icon: RadioTower, badge: "2" },
  { id: "logs", label: "运行日志", icon: FileClock },
] satisfies Array<{ id: PageId; label: string; icon: typeof Activity; badge?: string }>;

export function Sidebar({ active, onChange, open, onClose }: { active: PageId; onChange: (id: PageId) => void; open: boolean; onClose: () => void }) {
  const choose = (id: PageId) => {
    onChange(id);
    onClose();
  };

  return (
    <aside className={`sidebar ${open ? "sidebar--open" : ""}`}>
      <div className="traffic-lights" aria-hidden="true"><i /><i /><i /></div>
      <div className="sidebar-brand"><BrandMark compact /><div><strong>HD SPORTS</strong><span>PUSH TERMINAL</span></div></div>
      <nav className="sidebar-nav" aria-label="主导航">
        <span className="nav-group-label">工作台</span>
        {navigation.map(({ id, label, icon: Icon, badge }) => (
          <button key={id} type="button" className={active === id ? "active" : ""} onClick={() => choose(id)}>
            <Icon size={17} strokeWidth={1.8} /><span>{label}</span>{badge && <small>{badge}</small>}
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <button type="button" className={active === "settings" ? "active" : ""} onClick={() => choose("settings")}><Settings size={17} /><span>系统设置</span></button>
        <div className="runtime-card"><span className="runtime-icon"><Bot size={17} /></span><span><strong>推送服务</strong><small><i /> 运行稳定</small></span></div>
      </div>
      <button type="button" className="sidebar-close" aria-label="关闭导航" onClick={onClose}><ChevronLeft size={18} /></button>
    </aside>
  );
}
