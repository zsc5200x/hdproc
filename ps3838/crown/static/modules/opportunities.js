import { fetchJson } from "./http.js";

const EMPTY_HTML = `<div class="event-list-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="4"/></svg><strong>当前没有同线反向报价</strong><p>只有两边都报出同一条让球/大小球线、且方向相反时才会出现在这里；没有严格套利时显示最接近的候选。</p></div>`;

const TIERS = new Set(["arb", "critical", "near", "watch"]);

function ageText(seconds){
  const value=Number(seconds);
  if(!Number.isFinite(value))return "时间未知";
  if(value<60)return `${Math.max(0,Math.round(value))} 秒前`;
  if(value<3600)return `${Math.floor(value/60)} 分前`;
  return `${Math.floor(value/3600)} 小时前`;
}

function stateText(item){
  if(item.isLive)return `LIVE${item.minute?` ${item.minute}`:""}`;
  return item.startLabel||"待开";
}

function edgeText(edge){
  const value=Number(edge)||0;
  return `${value>=0?"+":""}${(value*100).toFixed(2)}%`;
}

/**
 * Same-line cross-book opportunity list. The server already ranks by edge and
 * only returns pairs that are quoted on both sides; this module is a thin
 * renderer plus a two-second poll so the panel follows the WSS stream.
 */
export function createOpportunities({ enabled = false, escapeHtml, onSelect = () => {} } = {}) {
  const esc = escapeHtml;
  const list = document.getElementById("opportunityList");
  const badge = document.getElementById("opportunityBadge");
  const totalNode = document.getElementById("opportunityTotal");
  const subNode = document.getElementById("opportunitySubStatus");
  let timer = null;
  let inFlight = false;
  let renderedSignature = "";

  function card(item){
    const tier = TIERS.has(String(item.tier)) ? String(item.tier) : "watch";
    return `<button class="opportunity-item ${tier}" type="button" data-crown-id="${esc(item.crownId)}">
      <span class="opportunity-head"><span class="opportunity-league">${esc(item.league)}</span><span class="opportunity-state">${esc(stateText(item))}</span></span>
      <span class="opportunity-teams"><span>${esc(item.home)}</span><strong>${esc(item.score||"VS")}</strong><span>${esc(item.away)}</span></span>
      <span class="opportunity-market">${esc(item.marketLabel)} ${esc(item.lineText)}</span>
      <span class="opportunity-quotes"><span class="side-crown">皇冠 ${esc(item.crownSideLabel)} ${Number(item.crownWater).toFixed(2)}</span><span class="side-pin">平博 ${esc(item.pinnacleSideLabel)} ${Number(item.pinnacleWater).toFixed(3)}</span></span>
      <span class="opportunity-foot"><span class="opportunity-edge ${tier}">${edgeText(item.edge)}</span><span>合水 ${Number(item.waterSum).toFixed(3)}</span><span>${esc(ageText(item.ageSeconds))}</span></span>
    </button>`;
  }

  function render(payload){
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const stats = payload?.stats || {};
    if(badge)badge.textContent = String(items.length);
    if(totalNode)totalNode.textContent = `${items.length} 条`;
    if(subNode){
      let text;
      if(Number(stats.arb) > 0)text = `${stats.arb} 条套利 · ${items.length} 条候选`;
      else if(items.length)text = `无严格套利 · 最接近 ${edgeText(items[0].edge)}`;
      else text = "当前没有同线反向报价";
      // The server drops pairs whose two events are not the same fixture
      // (youth/reserve/women's namesakes, rescheduled kickoffs). Surface the
      // count so a suddenly missing match is explainable.
      const mismatch = Number(stats.mismatch || 0);
      if(mismatch > 0)text += ` · 已忽略 ${mismatch} 条跨赛事配对`;
      const stale = Number(stats.stale || 0);
      if(stale > 0)text += ` · 已忽略 ${stale} 条过期实时盘`;
      subNode.textContent = text;
    }
    const signature = JSON.stringify(items.map((item) => [
      item.id, item.edge, item.waterSum, item.crownWater, item.pinnacleWater, item.ageSeconds,
    ]));
    if(signature === renderedSignature)return;
    renderedSignature = signature;
    if(list)list.innerHTML = items.length ? items.map(card).join("") : EMPTY_HTML;
  }

  async function refresh(){
    if(!enabled || inFlight || document.hidden)return;
    inFlight = true;
    try{
      const { response, payload } = await fetchJson("/api/merge/opportunities?limit=15", { cache: "no-store" });
      if(!response.ok || !payload || payload.ok === false)throw new Error("opportunity_unavailable");
      render(payload);
    }catch(error){
      if(subNode && !renderedSignature)subNode.textContent = "机会数据暂不可用，稍后自动重试";
    }finally{
      inFlight = false;
    }
  }

  function start(){
    if(!enabled || timer)return;
    refresh();
    timer = setInterval(refresh, 2000);
  }

  function stop(){
    if(timer){clearInterval(timer);timer = null;}
  }

  if(list){
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-crown-id]");
      if(!button)return;
      onSelect(String(button.dataset.crownId || ""));
    });
  }

  return { start, stop, refresh };
}
