import { ChevronRight, CircleAlert, Clock3, Send } from "lucide-react";
import type { PushOrder } from "../types";

const statusLabels = { sent: "已推送", failed: "失败", pending: "排队中" } as const;

export function OrderTable({ orders, density, onSelect }: { orders: PushOrder[]; density: "compact" | "comfortable"; onSelect: (order: PushOrder) => void }) {
  return (
    <div className={`order-table order-table--${density}`}>
      <div className="order-table__head" role="row">
        <span>时间 / 单号</span><span>联赛与赛事</span><span>玩法选择</span><span>金额</span><span>推送状态</span><span aria-hidden="true" />
      </div>
      <div className="order-table__body">
        {orders.length === 0 ? (
          <div className="empty-state"><CircleAlert size={20} /><span>没有符合条件的订单</span></div>
        ) : orders.map((item) => (
          <button type="button" className="order-row" key={item.id} onClick={() => onSelect(item)}>
            <span className="order-time"><strong>{item.time}</strong><small>{item.id}</small></span>
            <span className="order-match"><small>{item.league}</small><strong>{item.match}</strong></span>
            <span className="order-selection"><small>{item.phase} · {item.market} · {item.period}</small><strong>{item.selection}</strong></span>
            <span className="order-amount"><strong>¥{item.amount.toLocaleString()}</strong><small>{item.target}</small></span>
            <span className={`status-pill status-pill--${item.status}`}>
              {item.status === "sent" ? <Send size={13} /> : item.status === "pending" ? <Clock3 size={13} /> : <CircleAlert size={13} />}
              {statusLabels[item.status]}
            </span>
            <ChevronRight size={16} className="order-arrow" />
          </button>
        ))}
      </div>
    </div>
  );
}
