import { createConfirmedMarkets } from "./modules/confirmed-markets.js";
import { crownHandicapLine, crownNormalizeLine } from "./modules/line-format.js";
import { createOpportunities } from "./modules/opportunities.js";
import { crownPinnacleChange } from "./modules/pinnacle-format.js";

function installSourceRouter(){
  if(window.__sourceRouterInstalled)return;
  window.__sourceRouterInstalled=true;
  window.__sourceView=window.history.state?.sourceView||window.location.pathname+window.location.search;
  window.history.replaceState({...window.history.state,sourceView:window.__sourceView},"",window.location.href);
  document.addEventListener("click",event=>{
    const link=event.target.closest("a[href]");if(!link)return;
    const target=new URL(link.href,window.location.href);
    if(target.origin!==window.location.origin||!["/","/crown"].includes(target.pathname))return;
    event.preventDefault();window.location.assign(target.pathname+target.search);
  });
}
installSourceRouter();
function installSportsDialogA11y(){
  if(window.__sportsDialogA11y)return;
  window.__sportsDialogA11y=true;
  window.__openSportsDialog=(dialog,opener)=>{
    document.querySelectorAll('[role="dialog"].open').forEach(other=>{
      if(other!==dialog)other.classList.remove("open");
    });
    dialog.__returnFocus=opener||document.activeElement;
    dialog.classList.add("open");
    dialog.setAttribute("aria-hidden","false");
    const target=dialog.querySelector("[data-dialog-autofocus]")||dialog.querySelector("button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href]")||dialog;
    requestAnimationFrame(()=>target.focus({preventScroll:true}));
  };
  window.__closeSportsDialog=dialog=>{
    dialog.classList.remove("open");
    dialog.setAttribute("aria-hidden","true");
    const target=dialog.__returnFocus;
    if(target&&target.isConnected)target.focus({preventScroll:true});
  };
  document.addEventListener("keydown",event=>{
    const dialog=[...document.querySelectorAll('[role="dialog"].open')].at(-1);if(!dialog)return;
    if(event.key==="Escape"){event.preventDefault();window.__closeSportsDialog(dialog);return;}
    if(event.key!=="Tab")return;
    const items=[...dialog.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(item=>item.getClientRects().length);
    if(!items.length){event.preventDefault();dialog.focus();return;}
    const first=items[0],last=items[items.length-1];
    if(event.shiftKey&&(document.activeElement===first||!dialog.contains(document.activeElement))){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||!dialog.contains(document.activeElement))){event.preventDefault();first.focus();}
  });
}
installSportsDialogA11y();

const crown$=id=>document.getElementById(id);
const crownQuery=new URLSearchParams(window.location.search);
const crownEmbedded=crownQuery.get("embed")==="1";
// The homepage requests fixtures=1 for the combined Crown + Pinnacle view.
// The standalone Crown page stays Crown-only even though it is embedded too.
const crownFixtures=crownQuery.get("fixtures")==="1";
if(crownFixtures){
  crown$("eventsTitle").textContent="实时数据流";
  const eyebrow=crown$("eventsTitle").closest(".panel-title-row")?.querySelector(".eyebrow");
  // Keep the English kicker so it does not repeat the Chinese panel title.
  if(eyebrow)eyebrow.textContent="LIVE FEED";
  crown$("crownSubStatus").textContent="正在读取实时数据流…";
  crown$("crownSourceLabel").textContent="实时数据流";
  const loadingTitle=crown$("crownEvents").querySelector(".event-list-empty strong");
  if(loadingTitle)loadingTitle.textContent="正在读取实时数据流";
}
const crownCollapsedGroups=new Set();
let crownSelectedId="";
let crownLatestState={events:[],leagues:[],status:"检查中"};
let crownRenderedSignature="";
let crownRenderedAccountsSignature=null;
let crownUpdateInFlight=false;
let crownAccountGeneration=0;
let crownRefreshPaused=false;
let crownLastAccountsFetchAt=0;
let crownLastSessionProbeAt=0;
let crownSessionProbeInFlight=false;
const crownExtraAttempts=new Map();
const crownExtraLoading=new Set();
const crownAccountActionsInFlight=new Set();
const crownConfirmed = createConfirmedMarkets({
  enabled: crownFixtures,
  escapeHtml: crownEsc,
  onChange: () => { crownRenderedSignature = ""; crownRender(crownLatestState); },
});

function crownPostEmbeddedState(){
  if(!crownEmbedded||window.parent===window)return;
  window.parent.postMessage({type:"crown:state",paused:crownRefreshPaused,status:crownLatestState.status||"检查中",cached:Boolean(crownLatestState.cached),error:crownLatestState.error||null,eventCount:Array.isArray(crownLatestState.events)?crownLatestState.events.length:0,updatedAt:crownLatestState.updated_at||null},window.location.origin);
}
function crownSetPaused(paused){
  crownRefreshPaused=Boolean(paused);
  const button=crown$("pauseRefresh");
  if(button){button.setAttribute("aria-pressed",String(crownRefreshPaused));const label=button.querySelector("span");if(label)label.textContent=crownRefreshPaused?"继续刷新":"暂停刷新";}
  const pollState=crown$("crownPollState");if(pollState)pollState.textContent=crownRefreshPaused?"轮询已暂停":"轮询运行中";
  crownPostEmbeddedState();
}

function crownEsc(value){return String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function crownLive(event){return !!event.is_live;}
function crownFinished(event){
  if(event?.is_finished===true||event?.finished===true)return true;
  const finished=new Set(["FT","FULL_TIME","FULLTIME","FINISHED","FINAL","ENDED","END","COMPLETE","COMPLETED","已结束","完赛","已完赛"]);
  // Crown keeps finished matches on the live list with a stale minute such as
  // "2H^74:13"; its explicit period flag (FT) is the reliable signal.
  const phases=[event?.match_status,event?.finish_status,event?.period].map(value=>String(value??"").trim().toUpperCase().replace(/[\s-]+/g,"_"));
  if(phases.some(phase=>finished.has(phase)))return true;
  const minute=String(event?.minute??"").trim().toUpperCase().replace(/[\s-]+/g,"_");
  if(finished.has(minute))return true;
  const embedded=minute.match(/^([A-Z][A-Z0-9_-]*)\s*\^/);
  return Boolean(embedded&&finished.has(embedded[1]));
}
function crownFormatMatchPhase(minute,fallback){
  const rawMinute=String(minute??"").trim();
  const labels={"1H":"上半场","1ST":"上半场","1":"上半场","2H":"下半场","2ND":"下半场","2":"下半场","HT":"中场","HALF_TIME":"中场","HALFTIME":"中场","FT":"已结束","FINISHED":"已结束","FINAL":"已结束","ET":"加时赛","EXTRA_TIME":"加时赛","EXTRATIME":"加时赛","P":"点球","PEN":"点球","PENALTIES":"点球","LIVE":"进行中"};
  const embedded=rawMinute.match(/^([A-Za-z0-9][A-Za-z0-9_-]*)\s*\^\s*(.+)$/);
  if(embedded){
    const marker=embedded[1].toUpperCase().replace(/[\s-]+/g,"_");
    const value=embedded[2].trim();
    const label=labels[marker==="MTIME"?value.toUpperCase():marker];
    if(label)return marker==="MTIME"||label==="已结束"?label:`${label} ${value}`;
  }
  return labels[rawMinute.toUpperCase()]||rawMinute||fallback||"开赛时间未知";
}
function crownTimeLabel(value){
  if(!value)return "—";
  const date=new Date(String(value).replace(" ","T"));
  if(Number.isNaN(date.getTime()))return String(value);
  return new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(date);
}
function crownStatusKind(state){
  if(state.error==="session_conflict")return state.cached?"delayed":"loading";
  const status=String(state.status||"");
  if(status==="已连接"&&!state.error)return "live";
  if(/需要重新登录|过期|未连接/.test(status))return "offline";
  if(status==="数据延迟"||status==="缓存数据"||state.cached)return "delayed";
  if(state.error||/不可用|失败/.test(status))return "offline";
  return "loading";
}
function crownLoadedExtra(event){const extra=event&&event.extra_markets;return extra&&extra.status==="loaded"?extra:null;}
function crownLineKey(value){
  const text=String(value??"").normalize("NFKC").replace(/[–—−]/g,"-").replace(/\s+/g,"");
  const parts=text.split("/");
  const values=parts.map(Number);
  if(parts.length===1&&Number.isFinite(values[0]))return `n:${values[0].toFixed(3)}`;
  if(parts.length===2&&values.every(Number.isFinite)){
    if(parts[0].startsWith("-")&&!parts[1].startsWith("+")&&!parts[1].startsWith("-"))values[1]=-Math.abs(values[1]);
    return `n:${((values[0]+values[1])/2).toFixed(3)}`;
  }
  return `s:${text.toLowerCase()}`;
}
function crownMarketItems(event,kind){
  const base=event&&event.markets&&Array.isArray(event.markets[kind])?event.markets[kind]:[];
  const extra=crownLoadedExtra(event);
  const items=extra&&Array.isArray(extra[kind])?extra[kind]:[];
  if(!items.length)return base;
  if(kind==="moneyline")return base.length?base:items;
  const merged=[...base];
  const lines=new Set(base.map(item=>crownLineKey(item&& (item.raw_line||item.line))));
  items.forEach(item=>{
    const key=crownLineKey(item&& (item.raw_line||item.line));
    if(lines.has(key))return;
    lines.add(key);
    merged.push(item);
  });
  return merged;
}
function crownMoneylineValues(event){
  const base=Array.isArray(event&&event.markets&&event.markets.moneyline)?event.markets.moneyline:[];
  if(base.filter(value=>value!==undefined&&value!==null&&String(value)!=="").length>=3)return base;
  const extra=crownLoadedExtra(event);
  if(extra&&Array.isArray(extra.moneyline)&&extra.moneyline.length){
    const values=[];
    extra.moneyline.forEach(item=>{
      if(item&&typeof item==="object"){
        if("home" in item||"draw" in item||"away" in item)values.push(item.home,item.draw,item.away);
        else values.push(item.a,item.b);
      }else values.push(item);
    });
    if(values.filter(value=>value!==undefined&&value!==null&&String(value)!=="").length>=3)return values.slice(0,3);
  }
  return base;
}
function crownQuoteCount(event){
  return crownMarketItems(event,"handicap").length+crownMarketItems(event,"total").length+(crownMoneylineValues(event).length>=3?1:0);
}
function crownMarketStatusText(event,kind,count){
  if(crownLatestState.extra_markets_enabled===false)return `${count} 组盘口 · 附加盘采集已关闭`;
  const extra=event&&event.extra_markets;
  if(extra&&extra.status==="loaded"){
    const baseCount=event&&event.markets&&Array.isArray(event.markets[kind])?event.markets[kind].length:0;
    if(kind==="moneyline")return `${count} 组盘口`;
    if(kind!=="moneyline"&&count>baseCount)return `主盘 + ${count-baseCount} 组附加盘`;
    return `${count} 组盘口`;
  }
  if(extra&&extra.status==="unavailable")return `${count} 组盘口 · 附加盘暂不可用`;
  if(extra&&extra.status==="not_requested")return `${count} 组盘口 · 附加盘加载中`;
  return `${count} 组盘口`;
}
function crownGroupLabel(event){
  if(crownLive(event))return crownFormatMatchPhase(event.minute,event.start_label);
  return event.start_label||"开赛时间未知";
}
function crownMarketId(item){return item&&(item.gidm||item.market_id||item.gid||item.hgid)||"";}
function crownQuoteStaleNote(capturedAt){
  if(!capturedAt)return "";
  const time=new Date(String(capturedAt).replace(" ","T")).getTime();
  if(!Number.isFinite(time)||Date.now()-time<5*60*1000)return "";
  return `采集 ${crownTimeLabel(capturedAt)}`;
}
// Relative age for the last usable water of a suspended Pinnacle selection.
function crownAgeSeconds(capturedAt){
  if(!capturedAt)return null;
  const time=new Date(String(capturedAt).replace(" ","T")).getTime();
  if(!Number.isFinite(time))return null;
  return Math.max(0,Math.round((Date.now()-time)/1000));
}
function crownAgeNote(capturedAt){
  const seconds=crownAgeSeconds(capturedAt);
  if(seconds==null)return "时间未知";
  if(seconds<60)return `${seconds} 秒前`;
  if(seconds<3600)return `${Math.floor(seconds/60)} 分前`;
  return `${crownTimeLabel(capturedAt)} 采集`;
}
// Same semantics as the Pinnacle data panel: green for a rise, red for a
// drop, "新盘" for a newly created row, nothing while the row is flat.
function crownQuoteChange(quote){
  const change=crownPinnacleChange(quote);
  if(!change)return "";
  return `<span class="paired-change ${change.direction}">${crownEsc(change.text)}</span>`;
}
function crownPairedPrice(price,market,option,line,confirmed){
  const crownPrice=price==null||String(price)===""?"—":price;
  if(!crownFixtures||!confirmed)return crownEsc(crownPrice);
  const quote=crownConfirmed.quote(confirmed,market,option,line);
  // A suspended selection keeps its last usable water, clearly badged: this is
  // what stops the paired card from flickering to "—" on every Pinnacle pause.
  const suspended=Boolean(quote?.suspended);
  const pinnaclePrice=suspended?quote.lastPrice:(quote?.price??"—");
  const alternate=quote?.alternate||null;
  const quoteTitle=quote?.quoted
    ?quote.capturedAt?`平博赔率最后采集时间：${quote.capturedAt}`:"平博报价采集时间未知"
    :suspended
      ?`平博该选项暂停报价，显示最后有效水位（${quote.lastCapturedAt||"时间未知"}）`
      :quote?.reason==="no_matching_line"?"平博当前没有相同盘口线的报价记录":"平博该选项当前无报价";
  const suspendNote=suspended
    ?`<small class="paired-line-note suspended">暂停 · ${crownEsc(crownAgeNote(quote.lastCapturedAt))}</small>`
    :"";
  // No same line: show the closest Pinnacle line and its water instead of a
  // bare dash, labelled with Crown's split quarter notation.
  const alternateNote=!quote?.quoted&&!suspended&&alternate&&alternate.price!=="—"
    ?`<small class="paired-line-note">平博 ${crownEsc(alternate.line)} ${crownEsc(alternate.price)}</small>`
    :!quote?.quoted&&!suspended&&alternate&&alternate.lastPrice
    ?`<small class="paired-line-note suspended">平博 ${crownEsc(alternate.line)} ${crownEsc(alternate.lastPrice)} · 暂停</small>`
    :"";
  const quoteNote=quote&&!quote.quoted&&!suspended
    ?(alternateNote||`<small class="paired-line-note">${quote.reason==="no_matching_line"?"暂无同盘口":"该项无报价"}</small>`)
    :"";
  const staleNote=quote?.quoted?crownQuoteStaleNote(quote.capturedAt):"";
  const staleHtml=staleNote?`<small class="paired-line-note">${crownEsc(staleNote)}</small>`:"";
  const lineNote=quote?.lineDiff?`<small class="paired-line-note">平博盘 ${crownEsc(quote.line)}</small>`:"";
  const changeHtml=crownQuoteChange(quote);
  return `<span class="paired-odds"><span class="crown-odds">皇冠 ${crownEsc(crownPrice)}</span><span class="pinnacle-odds${suspended?" suspended":""}" title="${crownEsc(quoteTitle)}">平博 ${crownEsc(pinnaclePrice)}</span>${changeHtml}${suspendNote}${quoteNote}${lineNote}${staleHtml}</span>`;
}
function crownFilterEvents(events,query,filter){
  return events.filter(event=>{
    if(crownFinished(event))return false;
    if(filter==="live"&&!crownLive(event))return false;
    if(filter==="prematch"&&crownLive(event))return false;
    return !query||[event.league,event.home,event.away,event.id].join(" ").toLowerCase().includes(query);
  });
}
function crownEventItem(event){
  const quoteCount=crownQuoteCount(event);
  const score=event.score||"VS";
  return `<button class="event-item${event.id===crownSelectedId?" selected":""}" type="button" data-event-id="${crownEsc(event.id)}" aria-pressed="${event.id===crownSelectedId}">
    <span class="event-item-main">
      <span class="event-item-top">${crownLive(event)?'<i class="event-live-dot" aria-hidden="true"></i>':""}<span class="event-time">${crownEsc(crownGroupLabel(event))}</span></span>
      <span class="event-team-line"><span>${crownEsc(event.home||"主队")}</span><strong>${crownEsc((score.split("-")[0]||"").trim())}</strong></span>
      <span class="event-team-line"><span>${crownEsc(event.away||"客队")}</span><strong>${crownEsc((score.split("-")[1]||"").trim())}</strong></span>
      <span class="event-item-foot"><span>${crownEsc(event.league||"未命名联赛")}</span><span>${quoteCount} 组报价</span></span>
    </span>
    <span class="event-item-side"><strong>${crownLive(event)?"LIVE":"TODAY"}</strong><span>#${crownEsc(event.id||"—")}</span></span>
  </button>`;
}
function crownEventGroup(key,title,items,expandAll){
  if(!items.length)return "";
  const leagues=new Map();
  items.forEach(event=>{if(!leagues.has(event.league))leagues.set(event.league,[]);leagues.get(event.league).push(event);});
  const collapsed=crownCollapsedGroups.has(key);
  // Each league is its own drawer: a collapsed league renders only its header,
  // so its matches disappear until the header is expanded again. A search
  // query force-expands every league so matching fixtures are never hidden.
  const leagueHtml=[...leagues.entries()].map(([name,events])=>{
    const leagueKey=`${key}::${name}`;
    const leagueCollapsed=!expandAll&&crownCollapsedGroups.has(leagueKey);
    return `<div class="league-group${leagueCollapsed?" collapsed":""}"><button class="league-label" type="button" data-league-toggle="${crownEsc(leagueKey)}" aria-expanded="${!leagueCollapsed}"><strong>${crownEsc(name)}</strong><span>${events.length} 场</span><i class="league-toggle" aria-hidden="true">⌄</i></button>${leagueCollapsed?"":events.map(crownEventItem).join("")}</div>`;
  }).join("");
  return `<section class="event-group ${key}${collapsed?" collapsed":""}"><button class="event-group-head" type="button" data-group-toggle="${key}" aria-expanded="${!collapsed}"><span class="event-group-title">${title}</span><span class="event-group-count">${items.length} 场</span><span class="event-group-toggle" aria-hidden="true">⌄</span></button><div class="league-list">${leagueHtml}</div></section>`;
}
function crownMarketBook(title,items,kind,event,confirmed){
  const list=Array.isArray(items)?items:[];
  const statusText=crownMarketStatusText(event,kind,list.length);
  if(!list.length)return `<article class="market-book"><header class="market-book-head"><h3>${title}</h3><small>${crownEsc(statusText)}</small></header><div class="market-empty">当前没有有效盘口</div></article>`;
  const rows=list.map(item=>{
    const line=item.raw_line||item.line||"盘口未知";
    const pair=kind==="handicap"
      ? [[`${event.home||"主队"} ${crownHandicapLine(line,"home")}`,item.a,"0",crownHandicapLine(line,"home")],[`${event.away||"客队"} ${crownHandicapLine(line,"away")}`,item.b,"1",crownHandicapLine(line,"away")]]
      : [[`大 ${crownNormalizeLine(line)}`,item.a,"3",line],[`小 ${crownNormalizeLine(line)}`,item.b,"4",line]];
    const marketId=crownMarketId(item);
    return `<div class="market-line">${pair.map(([label,price,option,quoteLine])=>`<div class="selection"><span>${crownEsc(label)}</span><strong class="${crownFixtures&&confirmed?"paired-odds-value":""}">${crownPairedPrice(price,kind==="handicap"?"2":"3",option,quoteLine,confirmed)}</strong></div>`).join("")}</div>${marketId?`<div class="market-note">${item.master==="Y"||!item.gidm?"主盘":"附加盘"} · CROWN ${crownEsc(marketId)}</div>`:""}`;
  }).join("");
  return `<article class="market-book"><header class="market-book-head"><h3>${title}</h3><small>${crownEsc(statusText)}</small></header>${rows}</article>`;
}
function crownMoneyline(event,confirmed){
  const values=crownMoneylineValues(event);
  const statusText=crownMarketStatusText(event,"moneyline",values.length>=3?1:0);
  if(values.length<3)return `<article class="market-book moneyline"><header class="market-book-head"><h3>全场胜平负</h3><small>${crownEsc(statusText)}</small></header><div class="market-empty">当前没有有效报价</div></article>`;
  const labels=[event.home||"主队","和局",event.away||"客队"];
  // Crown returns home/draw/away; Pinnacle numbers 0=home, 1=away, 2=draw.
  const pinnacleOptions=["0","2","1"];
  return `<article class="market-book moneyline"><header class="market-book-head"><h3>全场胜平负</h3><small>${crownEsc(statusText)}</small></header><div class="moneyline-grid">${labels.map((label,index)=>`<div class="moneyline-cell"><span>${crownEsc(label)}</span><strong class="${crownFixtures&&confirmed?"paired-odds-value":""}">${crownPairedPrice(values[index]??"—","1",pinnacleOptions[index],"",confirmed)}</strong></div>`).join("")}</div></article>`;
}
function crownTeamBlock(crownName,pinnacleName,side){
  const name=String(crownName||"").trim()||"未知球队";
  // Always show the Pinnacle spelling for both sides so the confirmed pairing
  // can be checked even when the two feeds use the same display name.
  const alias=String(pinnacleName||"").trim();
  return `<div class="match-team${side==="away"?" away":""}"><span class="match-team-name">${crownEsc(name)}</span>${alias?`<small class="match-team-alias">（平博 ${crownEsc(alias)}）</small>`:""}</div>`;
}
function crownEventDetail(event){
  if(!event)return `<div class="detail-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg><strong>等待皇冠赛事数据</strong><p>打开皇冠登录并完成会话验证后，赛事和盘口会自动显示在这里。</p><button class="button primary" type="button" data-open-login>连接皇冠会话</button></div>`;
  const confirmed=crownConfirmed.get(event);
  event=crownConfirmed.mergeEvent(event,confirmed);
  const markets=event.markets||{};
  const handicapItems=crownMarketItems(event,"handicap");
  const totalItems=crownMarketItems(event,"total");
  const score=event.score||"VS";
  const detail=crownFormatMatchPhase(event.minute,event.start_label);
  const pinnacle=confirmed&&(confirmed.pinnacle||confirmed.pinnacleEvent);
  const pinnacleId=String(pinnacle?.id||confirmed?.pinnacleId||"");
  const pinnacleAge=confirmed?crownAgeSeconds(pinnacle?.lastCapturedAt):null;
  const sourceLabel=confirmed
    ?(pinnacleAge!=null&&pinnacleAge>300?`皇冠 + 平博（平博数据停在 ${crownAgeNote(pinnacle.lastCapturedAt)}）`:"皇冠 + 平博")
    :"当前项目";
  const crownLeagueId=String(event.leagueId||event.league_id||"");
  const pinnacleLeagueId=String(pinnacle?.leagueId||"");
  const leagueIdPills=[
    crownLeagueId?`<span class="league-id-pill crown">皇冠联赛 ID ${crownEsc(crownLeagueId)}</span>`:"",
    pinnacleLeagueId?`<span class="league-id-pill pinnacle"${pinnacle?.league?` title="${crownEsc(pinnacle.league)}"`:""}>平博联赛 ID ${crownEsc(pinnacleLeagueId)}</span>`:"",
  ].filter(Boolean).join("");
  const matchIdPills=[
    confirmed?'<span class="confirmed-chip">已确认关联</span>':"",
    `<span class="match-id-pill">皇冠比赛 ID ${crownEsc(event.id||"—")}</span>`,
    crownFixtures?`<span class="match-id-pill pinnacle">平博比赛 ID ${crownEsc(pinnacleId||"待匹配")}</span>`:"",
  ].filter(Boolean).join("");
  const crownHeader=crownFixtures?"":`<div class="markets-provider-title crown-provider-title"><div><strong>皇冠盘口</strong><span>${crownEsc(event.odds_format_label||"皇冠港赔（HK）")}</span></div><small>皇冠 ID ${crownEsc(event.id||"—")}</small></div>`;
  return `<article class="event-hero"><div class="match-league"><div class="match-league-head"><strong class="match-league-name">${crownEsc(event.league||"未命名联赛")}</strong><span class="match-state${crownLive(event)?" live":""}">${crownLive(event)?"滚球":"今日待开"}</span></div>${leagueIdPills?`<div class="match-league-ids">${leagueIdPills}</div>`:""}</div><div class="match-hero">${crownTeamBlock(event.home||"主队",pinnacle?.home,"home")}<div class="match-score"><strong>${crownEsc(score)}</strong><span>${crownEsc(detail)}</span></div>${crownTeamBlock(event.away||"客队",pinnacle?.away,"away")}</div><div class="match-ids">${matchIdPills}</div></article><div class="detail-summary-row"><div><span>皇冠报价组数</span><strong>${crownQuoteCount(event)} 组</strong></div><div><span>盘口格式</span><strong>${crownEsc(event.odds_format_label||"皇冠港赔（HK）")}</strong></div><div><span>数据来源</span><strong>${crownEsc(sourceLabel)}</strong></div></div><div class="markets-layout">${crownHeader}${crownMoneyline(event,confirmed)}${crownMarketBook("让球",handicapItems,"handicap",event,confirmed)}${crownMarketBook("大小球",totalItems,"total",event,confirmed)}</div>`;
}
function crownRender(state){
  crownLatestState=state;
  crownPostEmbeddedState();
  const events=Array.isArray(state.events)?state.events:[];
  const activeEvents=events.filter(event=>!crownFinished(event));
  const query=crown$("crownSearch").value.trim().toLowerCase();
  const filter=crownFixtures?"":crown$("crownFilter").value;
  const visible=crownFilterEvents(activeEvents,query,filter);
  if(!visible.some(event=>event.id===crownSelectedId))crownSelectedId=visible[0]?.id||"";
  const selected=visible.find(event=>event.id===crownSelectedId)||null;
  if(selected)crownLoadExtra(selected);
  if(selected)crownConfirmed.load(selected);
  const liveCount=activeEvents.filter(crownLive).length;
  const todayCount=activeEvents.length-liveCount;
  const leagueCount=new Set(activeEvents.map(event=>event.league).filter(Boolean)).size;
  const quoteCount=activeEvents.reduce((total,event)=>total+crownQuoteCount(event),0);
  const kind=crownStatusKind(state);
  const syncingSession=state.error==="session_conflict";
  const status=syncingSession?"正在同步会话":state.status||"检查中";
  crown$("crownCount").textContent=activeEvents.length;
  crown$("crownLiveCount").textContent=`滚球 ${liveCount}`;
  crown$("crownTodayCount").textContent=`今日 ${todayCount}`;
  crown$("crownLeagueCount").textContent=leagueCount;
  crown$("crownVisibleCount").textContent=`显示 ${visible.length}`;
  crown$("crownVisibleBadge").textContent=visible.length;
  crown$("crownStatus").textContent=status;
  crown$("crownDataState").textContent=kind==="live"?"实时更新":state.cached?"缓存数据":kind==="delayed"?"数据延迟":kind==="offline"?"连接异常":"等待数据";
  crown$("crownStateBadge").textContent=kind==="live"?"在线":state.cached?"缓存":kind==="delayed"?"延迟":kind==="offline"?"异常":"检查中";
  crown$("crownStateBadge").className=`micro-badge ${kind==="live"?"live":kind==="offline"?"stale":"delayed"}`;
  crown$("crownSubStatus").textContent=state.updated_at?`${state.cached?"缓存数据 · 上次更新":"最后更新"} ${crownTimeLabel(state.updated_at)} · 只读轮询`:crownFixtures?"等待实时数据":"等待皇冠会话数据";
  crown$("crownQuoteTotal").textContent=`${quoteCount} 组报价`;
  const cachedUpstreamError=Boolean(state.cached)&&["upstream_unavailable","upstream_timeout","upstream_connection_failed","upstream_http_error"].includes(state.error);
  const errorText={session_conflict:"皇冠拒绝的是系统保存的旧会话，这不代表当前浏览器标签未登录。系统正在检查并同步当前皇冠标签，期间暂显示最后一次成功数据。",session_expired:"皇冠会话已失效，请在当前皇冠标签重新登录。",session_unverified:"皇冠会话仍在验证，系统会继续检查当前皇冠标签。",upstream_unavailable:cachedUpstreamError?"皇冠标签会话已保留，但盘口接口暂时不可用；页面保留最近成功数据，并按退避间隔自动重试。":"皇冠上游暂时不可用，页面会保留最近数据并按退避间隔自动重试。",upstream_timeout:cachedUpstreamError?"皇冠标签会话已保留，但盘口接口响应超时；页面保留最近成功数据，并按退避间隔自动重试。":"皇冠上游响应超时，页面会保留最近数据并按退避间隔自动重试。",upstream_connection_failed:cachedUpstreamError?"皇冠标签会话已保留，但盘口接口连接失败；页面保留最近成功数据，并按退避间隔自动重试。":"皇冠上游暂时连接失败，系统会按退避间隔自动重试。",upstream_http_error:"皇冠上游返回异常状态，页面会自动退避重试。",upstream_response_invalid:"皇冠上游响应格式异常，页面会自动重试。",catalog_incomplete:"皇冠赛事目录暂未完整返回，页面会保留最近数据并重试。",catalog_changed:"皇冠赛事目录发生变化，等待下一次完整同步。"}[state.error]||"皇冠数据暂时不可用，请检查当前登录标签或稍后重试。";
  crown$("crownErrorText").textContent=state.cached&&state.updated_at?`${errorText} 当前展示 ${crownTimeLabel(state.updated_at)} 的最后成功缓存。`:errorText;
  crown$("crownError").classList.toggle("hidden",crownFixtures||(!syncingSession&&kind!=="offline"&&kind!=="delayed"));
  const signature=JSON.stringify([visible,state.leagues||[],state.status,state.error,state.cached,state.updated_at,query,filter,selected?.id||"",[...crownCollapsedGroups].sort()]);
  if(signature===crownRenderedSignature)return;
  crownRenderedSignature=signature;
  const live=visible.filter(crownLive);
  const prematch=visible.filter(event=>!crownLive(event));
  const groupHtml=`${crownEventGroup("live","滚球比赛",live,Boolean(query))}${crownEventGroup("prematch","今日比赛",prematch,Boolean(query))}`;
  const endedOnly=events.length>0&&activeEvents.length===0;
  const emptyTitle=endedOnly?"暂无未结束赛事":crownFixtures?"暂无赛事数据":"暂无皇冠赛事";
  const emptyText=endedOnly?"":query?"没有匹配当前搜索条件的赛事。":crownFixtures?"当前暂时没有可显示的赛事。":"完成皇冠登录后，滚球和今日赛事会自动出现在这里。";
  crown$("crownEvents").innerHTML=groupHtml||`<div class="event-list-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg><strong>${emptyTitle}</strong>${emptyText?`<p>${emptyText}</p>`:""}${!query&&!crownFixtures&&!endedOnly?'<button class="button primary" type="button" data-open-login>连接皇冠会话</button>':""}</div>`;
  crown$("crownEventDetail").innerHTML=crownEventDetail(selected);
}
async function crownLoadExtra(event){
  if(crownLatestState.extra_markets_enabled===false)return;
  const gid=String(event&&event.id||"");
  const status=String(event&&event.extra_markets&&event.extra_markets.status||"");
  if(!gid||status==="loaded"||crownExtraLoading.has(gid))return;
  const lastAttempt=Number(crownExtraAttempts.get(gid)||0);
  if(Date.now()-lastAttempt<15000)return;
  crownExtraAttempts.set(gid,Date.now());
  crownExtraLoading.add(gid);
  try{
    const response=await fetch(`/api/crown/events/${encodeURIComponent(gid)}/details`,{method:"POST",cache:"no-store"});
    if(response.ok){crownRenderedSignature="";await crownUpdate(false);}
  }catch(error){
    // The regular polling loop retries after the cooldown.
  }finally{
    crownExtraLoading.delete(gid);
  }
}
function crownSessionView(state){
  const connected=state.status==="已连接";
  const syncingSession=state.error==="session_conflict";
  const delayed=state.status==="数据延迟"||state.status==="缓存数据"||state.cached;
  crown$("loginIndicator").className=`login-status-indicator ${connected?"connected":state.error?"error":""}`;
  crown$("loginStatusTitle").textContent=connected?"皇冠会话已连接":syncingSession?"皇冠会话 · 正在同步":`皇冠会话${state.status?" · "+state.status:""}`;
  crown$("loginStatusCopy").textContent=connected?`只读赛事接口正常，最近更新：${state.updated_at||"刚刚"}`:syncingSession?"皇冠拒绝了系统保存的旧会话，正在从当前皇冠标签同步；这不代表浏览器当前页面未登录。":delayed?`当前展示上次成功缓存，服务会自动恢复实时连接。最近更新：${state.updated_at||"未知"}`:"保存账号后，请在同一个 Chrome 窗口的皇冠普通标签页中手动登录。";
}
function crownAccountStatus(account){
  const labels={idle:"未登录",logging_in:"等待页面完成",checking:"检查中",awaiting_manual:"等待手动登录",session_received:"已保存会话",authenticated:"已验证",expired:"会话已过期",disabled:"已停用"};
  return labels[account.status]||account.status||"未知";
}
function crownRenderAccounts(accounts){
  const root=crown$("crownAccounts");
  if(!accounts.length){root.innerHTML='<div class="account-empty">当前项目还没有添加皇冠账号。</div>';return;}
  root.innerHTML=accounts.map(account=>{
    const session=account.has_session?"已保存会话":"未保存会话";
    const checked=account.last_checked?` · 最后检查 ${crownEsc(account.last_checked)}`:"";
    return `<div class="account-row" data-account-id="${crownEsc(account.id)}"><div class="account-row-head"><div><div class="account-row-name">${crownEsc(account.username)}</div><div class="account-row-meta">${crownEsc(account.base_url)} · ${session}${checked}</div></div><div class="account-row-status">${crownEsc(crownAccountStatus(account))}</div></div><div class="account-row-actions"><button class="action-button" data-account-action="login">打开登录页</button><button class="action-button" data-account-action="check">检查会话</button><button class="action-button" data-account-action="relogin">清除并重新登录</button><button class="action-button" data-account-action="cancel">取消</button><button class="action-button" data-account-action="clear">清除会话</button><button class="action-button danger" data-account-action="delete">删除</button></div></div>`;
  }).join("");
}
function crownApplyAccounts(accounts,forceRender=false){
  const accountList=Array.isArray(accounts)?accounts:[];
  const accountSignature=JSON.stringify(accountList);
  if(forceRender||accountSignature!==crownRenderedAccountsSignature){
    crownRenderedAccountsSignature=accountSignature;
    crownRenderAccounts(accountList);
  }
  return accountList;
}
function crownSetAccountsStatus(message){
  const status=crown$("crownAccountsStatus");
  if(status)status.textContent=message||"";
}
async function crownRefreshAccounts(forceRender=false){
  const generation=crownAccountGeneration;
  const response=await fetch("/api/crown/accounts",{cache:"no-store"});
  if(!response.ok)throw new Error("账号列表刷新失败");
  const payload=await response.json();
  if(generation!==crownAccountGeneration)return false;
  crownLastAccountsFetchAt=Date.now();
  crownApplyAccounts(payload.accounts,forceRender);
  return true;
}
async function crownUpdate(forceAccounts=false){
  if(crownUpdateInFlight)return;
  crownUpdateInFlight=true;
  try{
    const loginDialogOpen=crown$("loginModal").classList.contains("open");
    const accountPollInterval=loginDialogOpen?4000:(crownLatestState.error?3000:15000);
    const includeAccounts=forceAccounts||Date.now()-crownLastAccountsFetchAt>=accountPollInterval;
    const accountGeneration=crownAccountGeneration;
    const accountPromise=includeAccounts?fetch("/api/crown/accounts",{cache:"no-store"}).catch(()=>null):null;
    const eventResponse=await fetch("/api/crown/events?compact=1",{cache:"no-store"});
    if(!eventResponse.ok)throw new Error("HTTP error");
    const data=await eventResponse.json();
    crownRender(data);crownSessionView(data);
    if(data.error&&["session_conflict","session_expired","session_unverified"].includes(data.error)){
      const now=Date.now();
      if(!crownSessionProbeInFlight&&now-crownLastSessionProbeAt>=8000){
        crownLastSessionProbeAt=now;
        crownSessionProbeInFlight=true;
        fetch("/api/crown/session/check",{method:"POST",cache:"no-store"})
          .then(response=>response.ok?response.json():null)
          .then(()=>crownUpdate(true))
          .catch(()=>{})
          .finally(()=>{crownSessionProbeInFlight=false;});
      }
    }
    if(accountPromise){
      const accountResponse=await accountPromise;
      crownLastAccountsFetchAt=Date.now();
      if(accountResponse&&accountResponse.ok&&accountGeneration===crownAccountGeneration){
        const accounts=await accountResponse.json();
        if(accountGeneration===crownAccountGeneration)crownApplyAccounts(accounts.accounts);
      }
    }
  }catch(error){
    crown$("crownStatus").textContent="读取失败";
    crown$("crownStateBadge").textContent="失败";
    crown$("crownStateBadge").className="micro-badge stale";
    crown$("crownErrorText").textContent="无法读取皇冠接口，请确认当前项目服务和后端仍然正常运行。";
    crown$("crownError").classList.toggle("hidden",crownFixtures);
  }finally{crownUpdateInFlight=false;}
}
async function crownCheck(){
  const button=crown$("checkCrownSession");button.disabled=true;button.textContent="检查中…";
  try{await fetch("/api/crown/session/check",{method:"POST",headers:{"Content-Type":"application/json"}});}finally{button.disabled=false;button.textContent="检查全部会话";await Promise.allSettled([crownRefreshAccounts(true),crownUpdate()]);}
}
async function crownAccountAction(id,action){
  if(crownAccountActionsInFlight.has(id))return;
  crownAccountActionsInFlight.add(id);
  const root=crown$("crownAccounts");
  const row=[...root.querySelectorAll("[data-account-id]")].find(item=>item.dataset.accountId===id);
  const button=row&&row.querySelector(`[data-account-action="${action}"]`);
  const rowButtons=row?[...row.querySelectorAll("button")]:[];
  if(row)row.setAttribute("aria-busy","true");
  rowButtons.forEach(item=>{item.disabled=true;});
  if(button)button.textContent="处理中…";
  crownSetAccountsStatus("");
  try{
    const response=await fetch(`/api/crown/accounts/${encodeURIComponent(id)}/${action}`,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
    });
    const payload=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(payload.error||"操作失败");
    crownAccountGeneration+=1;
    if(Array.isArray(payload.accounts))crownApplyAccounts(payload.accounts,true);
    else await crownRefreshAccounts(true);
    const messages={clear:"已清除保存的会话，账号仍保留。",delete:"账号及本地保存的会话已永久删除。",cancel:"已取消当前登录任务。",check:"会话检查已完成。",login:"已打开皇冠普通标签页，请手动登录。",relogin:"已清除旧会话并打开皇冠普通标签页。"};
    crownSetAccountsStatus(messages[action]||"账号操作已完成。");
  }catch(error){
    crownSetAccountsStatus(error.message||"操作失败");
    window.alert(error.message||"操作失败");
  }finally{
    crownAccountActionsInFlight.delete(id);
    if(row?.isConnected){
      row.removeAttribute("aria-busy");
      rowButtons.forEach(item=>{item.disabled=false;});
      if(button)button.textContent={login:"打开登录页",check:"检查会话",relogin:"清除并重新登录",cancel:"取消",clear:"清除会话",delete:"删除"}[action]||"操作";
    }
  }
}
function settingsOpen(){window.__openSportsDialog(crown$("settingsModal"),crown$("settingsButton"));}
function settingsClose(){window.__closeSportsDialog(crown$("settingsModal"));}
function loginOpen(){window.__openSportsDialog(crown$("loginModal"),crown$("joinSessionButton"));crownRefreshAccounts().catch(()=>{});}
crown$("crownAccountForm").addEventListener("submit",async event=>{
  event.preventDefault();
  const status=crown$("crownAccountFormStatus");
  const button=event.currentTarget.querySelector("button[type=submit]");
  button.disabled=true;status.textContent="正在保存账号并打开皇冠标签页…";
  const payload={login_url:crown$("crownLoginUrl").value.trim(),username:crown$("crownUsername").value.trim()};
  try{
    const response=await fetch("/api/crown/accounts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||"添加账号失败");
    status.textContent="账号已保存。请在同一个 Chrome 窗口的皇冠普通标签页中手动登录。";
    crownAccountGeneration+=1;
    await Promise.allSettled([crownRefreshAccounts(true),crownUpdate()]);
  }catch(error){status.textContent=error.message||"添加账号失败";}
  finally{button.disabled=false;}
});
crown$("settingsButton").addEventListener("click",settingsOpen);
crown$("settingsClose").addEventListener("click",settingsClose);
crown$("settingsModal").addEventListener("click",event=>{if(event.target===crown$("settingsModal"))settingsClose();});
crown$("crownLoginButton").addEventListener("click",()=>{settingsClose();loginOpen();});
crown$("joinSessionButton").addEventListener("click",loginOpen);
crown$("closeLogin").addEventListener("click",()=>window.__closeSportsDialog(crown$("loginModal")));
crown$("crownEvents").addEventListener("click",event=>{
  const loginButton=event.target.closest("[data-open-login]");if(loginButton){loginOpen();return;}
  const leagueButton=event.target.closest("[data-league-toggle]");if(leagueButton){const key=leagueButton.dataset.leagueToggle;if(crownCollapsedGroups.has(key))crownCollapsedGroups.delete(key);else crownCollapsedGroups.add(key);crownRenderedSignature="";crownRender(crownLatestState);return;}
  const groupButton=event.target.closest("[data-group-toggle]");if(groupButton){const key=groupButton.dataset.groupToggle;if(crownCollapsedGroups.has(key))crownCollapsedGroups.delete(key);else crownCollapsedGroups.add(key);crownRender(crownLatestState);return;}
  const item=event.target.closest("[data-event-id]");if(!item)return;crownSelectedId=item.dataset.eventId;crownRender(crownLatestState);
});
crown$("crownEventDetail").addEventListener("click",event=>{
  if(event.target.closest("[data-open-login]")){loginOpen();return;}
});
crown$("checkCrownSession").addEventListener("click",crownCheck);
crown$("crownAccounts").addEventListener("click",event=>{const button=event.target.closest("[data-account-action]");if(!button)return;const row=button.closest("[data-account-id]");if(!row)return;const action=button.dataset.accountAction;const username=row.querySelector(".account-row-name")?.textContent||"该账号";if(action==="delete"&&!window.confirm(`确认永久删除“${username}”？这会删除本地保存的账号和会话，且无法撤销。`))return;crownAccountAction(row.dataset.accountId,action);});
crown$("crownSearch").addEventListener("input",()=>crownRender(crownLatestState));
crown$("crownFilter").addEventListener("change",()=>crownRender(crownLatestState));
// Refreshing stays automatic; the former manual controls are intentionally removed.
if(crownEmbedded){
  window.addEventListener("message",event=>{
    if(event.origin!==window.location.origin||event.source!==window.parent)return;
    const data=event.data||{};if(data.type!=="crown:command")return;
    if(data.command==="refresh")crownUpdate(true);
    else if(data.command==="open-login")loginOpen();
    else if(data.command==="set-paused")crownSetPaused(Boolean(data.paused));
  });
  crownPostEmbeddedState();
}
if(new URLSearchParams(window.location.search).get("openSettings")==="1")settingsOpen();
const crownOpportunities = crownFixtures ? createOpportunities({
  enabled: true,
  escapeHtml: crownEsc,
  onSelect: crownId => {
    if(!crownId)return;
    crownSelectedId=crownId;
    crownRenderedSignature="";
    crownRender(crownLatestState);
  },
}) : null;
crownUpdate();
setInterval(()=>{if(!crownRefreshPaused)crownUpdate();},1000);
if(crownOpportunities)crownOpportunities.start();
