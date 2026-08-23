import { CheckCircle2, Clock3, Copy, RotateCw, Send, X } from "lucide-react";
import { useState } from "react";
import type { PushOrder } from "../types";

export function OrderDrawer({ order, onClose }: { order: PushOrder; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  const copyMessage = async () => {
    await navigator.clipboard?.writeText(order.message);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="order-drawer" role="dialog" aria-modal="true" aria-label="订单详情">
        <header className="drawer-header">
          <div><span className="eyebrow">{order.id}</span><h2>订单详情</h2></div>
          <button type="button" className="icon-button" aria-label="关闭订单详情" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="drawer-scroll">
          <section className="drawer-match">
            <small>{order.league}</small>
            <h3>{order.match}</h3>
            <div className="drawer-tags"><span>{order.phase}</span><span>{order.market}</span><span>{order.period}</span></div>
          </section>
          <dl className="detail-grid">
            <div><dt>选择</dt><dd>{order.selection}</dd></div>
            <div><dt>金额</dt><dd>¥{order.amount.toLocaleString()}</dd></div>
            <div><dt>目标账号</dt><dd>{order.target}</dd></div>
            <div><dt>推送频道</dt><dd>{order.channel}</dd></div>
          </dl>
          <section className="timeline-section">
            <div className="section-heading"><div><span className="eyebrow">DELIVERY TRACE</span><h3>TG 推送记录</h3></div><span className={`status-pill status-pill--${order.status}`}>{order.status === "failed" ? "等待重试" : order.status === "pending" ? "队列等待" : "发送成功"}</span></div>
            <ol className="push-timeline">
              <li className="done"><CheckCircle2 size={16} /><span><strong>订单已捕获</strong><small>{order.time}.126</small></span></li>
              <li className="done"><Send size={16} /><span><strong>推送文本已生成</strong><small>{order.time}.284</small></span></li>
              <li className={order.status === "sent" ? "done" : order.status === "failed" ? "error" : "pending"}>{order.status === "failed" ? <RotateCw size={16} /> : <Clock3 size={16} />}<span><strong>{order.status === "sent" ? "Telegram 已送达" : order.status === "failed" ? "Telegram 请求重试" : "等待 Telegram 队列"}</strong><small>{order.latency}</small></span></li>
            </ol>
          </section>
          <section className="message-section">
            <div className="section-heading"><div><span className="eyebrow">MESSAGE PREVIEW</span><h3>推送文本</h3></div><button type="button" className="icon-button" aria-label="复制推送文本" title="复制推送文本" onClick={copyMessage}>{copied ? <CheckCircle2 size={17} /> : <Copy size={17} />}</button></div>
            <pre>{order.message}</pre>
          </section>
        </div>
      </aside>
    </div>
  );
}
