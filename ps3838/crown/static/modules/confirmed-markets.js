import { fetchJson } from "./http.js";
import { crownPinnaclePrice } from "./pinnacle-format.js";

function numericLine(value){
  const text=String(value??"").normalize("NFKC").replace(/[–—−]/g,"-").replace(/\s+/g,"");
  if(!text)return null;
  const parts=text.split("/");
  if(parts.length>2||parts.some(part=>!part))return null;
  const values=parts.map(Number);
  if(values.every(Number.isFinite)){
    if(parts.length===2){
      if(parts[0].startsWith("-")&&!parts[1].startsWith("+")&&!parts[1].startsWith("-"))values[1]=-Math.abs(values[1]);
      if(parts[0].startsWith("+")&&!parts[1].startsWith("+")&&!parts[1].startsWith("-"))values[1]=Math.abs(values[1]);
    }
    return values.reduce((sum,value)=>sum+value,0)/values.length;
  }
  return null;
}

function lineText(value){
  return String(value??"").normalize("NFKC").replace(/[–—−]/g,"-").replace(/\s+/g,"");
}

function canonicalLine(value){
  const number=numericLine(value);
  if(number!=null)return `n:${number.toFixed(3)}`;
  return `s:${lineText(value).toLowerCase()}`;
}

function trimmedNumber(value){
  return String(Number(value.toFixed(2)));
}

// Crown writes quarter-goal lines as split halves ("0/0.5" for 0.25,
// "0.5/1" for 0.75). Use the same notation for Pinnacle so both sides of a
// paired row read identically.
function quarterLineText(number){
  if(number==null||!Number.isFinite(number))return "";
  if(Math.abs(number)<0.000001)return "0";
  const magnitude=Math.abs(number);
  const steps=Math.round(magnitude*4);
  const sign=number<0?"-":"";
  if(Math.abs(magnitude*4-steps)<0.000001&&steps%2===1){
    return `${sign}${trimmedNumber(magnitude-0.25)}/${trimmedNumber(magnitude+0.25)}`;
  }
  return `${sign}${trimmedNumber(magnitude)}`;
}

function orientedLineText(value,market=""){
  const number=numericLine(value);
  if(number==null)return lineText(value);
  const formatted=quarterLineText(number);
  return market==="2"&&number>0?`+${formatted}`:formatted;
}

function rowIsQuoted(row){
  return row?.quoted!==false&&(row?.hongKong!=null||Boolean(row?.hongKongLabel));
}

function nearestRow(rows,market,option,target){
  const wanted=numericLine(target);
  if(wanted==null)return null;
  let best=null;
  let distance=Infinity;
  for(const row of rows){
    if(String(row?.market||"")!==String(market)||String(row?.option||"")!==String(option))continue;
    const value=numericLine(row?.line);
    if(value==null)continue;
    const gap=Math.abs(value-wanted);
    if(gap<distance-0.000001){best=row;distance=gap;}
  }
  return best&&distance>0.000001?best:null;
}

/** Only the fixtures homepage requests and renders confirmed Pinnacle markets. */
export function createConfirmedMarkets({ enabled = false, escapeHtml, onChange = () => {} }) {
  const crownFixtures = enabled;
  const crownEsc = escapeHtml;
  const crownConfirmedById = new Map();
  const crownConfirmedLoading = new Set();
  const crownConfirmedAttempts = new Map();
  function crownConfirmedFor(event){
    if(!crownFixtures)return null;
    const id=String(event&&event.id||"");
    return id?crownConfirmedById.get(id)||null:null;
  }
  function crownConfirmedCrownEvent(event,confirmed){
    const source=confirmed&&((confirmed.crown||confirmed.crownEvent));
    if(!source||typeof source!=="object")return event;
    // Keep the live Crown row (score, minute and current markets) ahead of the
    // persisted mapping snapshot while using the Crown snapshot as a fallback.
    return {...source,...event,markets:event.markets||source.markets||{},extra_markets:event.extra_markets||source.extra_markets||{}};
  }
  function crownPinnacleQuoteFor(confirmed,market,option,line){
    if(!crownFixtures||!confirmed)return null;
    const pinnacle=confirmed.pinnacle||confirmed.pinnacleEvent||{};
    const rows=Array.isArray(pinnacle.odds)?pinnacle.odds:[];
    const key=canonicalLine(line);
    const row=rows.find(item=>String(item?.market||"")===String(market)
      &&String(item?.option||"")===String(option)
      &&canonicalLine(item?.line)===key);
    if(!row){
      const fallback=nearestRow(rows,market,option,line);
      let alternate=null;
      if(fallback){
        const fallbackQuoted=rowIsQuoted(fallback);
        const fallbackLast=fallbackQuoted?"":crownPinnaclePrice({
          ...fallback,
          quoted:true,
          hongKong:fallback.lastHongKong,
          hongKongLabel:fallback.lastHongKongLabel,
        });
        alternate={
          line:orientedLineText(fallback.line,String(market)),
          price:crownPinnaclePrice(fallback),
          quoted:fallbackQuoted,
          capturedAt:String(fallback?.capturedAt||""),
          // Keep the nearest line useful while Pinnacle has that row suspended.
          lastPrice:fallbackLast!=="—"?fallbackLast:"",
          lastCapturedAt:String(fallback?.lastQuotedAt||""),
        };
      }
      return {
        price:"—",
        line:orientedLineText(line,String(market)),
        lineDiff:false,
        quoted:false,
        reason:"no_matching_line",
        capturedAt:"",
        direction:"",
        delta:null,
        alternate,
      };
    }
    const actualLine=orientedLineText(row.line,String(market));
    const quoted=rowIsQuoted(row);
    // Pinnacle suspends a selection by sending an empty price. The store keeps
    // the last quoted water so the paired card can keep showing a number with a
    // "suspended" badge instead of dropping to a bare dash between ticks.
    const lastPrice=quoted?"":crownPinnaclePrice({
      ...row,
      quoted:true,
      hongKong:row.lastHongKong,
      hongKongLabel:row.lastHongKongLabel,
    });
    const hasLast=!quoted&&lastPrice!=="—";
    const quote = {
      price:quoted?crownPinnaclePrice(row):"—",
      line:actualLine,
      lineDiff:canonicalLine(actualLine)!==canonicalLine(line),
      quoted,
      reason:quoted?"":"no_quote",
      capturedAt:String(row?.capturedAt||""),
      // The WSS pipeline already records the last movement for each row; the
      // homepage renders it exactly like the Pinnacle data panel.
      direction:quoted?String(row?.direction||"flat"):"",
      delta:quoted&&row?.delta!=null?Number(row.delta):null,
    };
    // Only a suspended selection carries the extra "last usable water" fields,
    // so the quoted shape stays unchanged for existing consumers.
    if(hasLast){
      quote.lastPrice=lastPrice;
      quote.lastCapturedAt=String(row?.lastQuotedAt||"");
      quote.suspended=true;
    }
    return quote;
  }
  function crownConfirmedSummary(confirmed,event){
    if(!confirmed)return "";
    const pinnacle=confirmed.pinnacle||confirmed.pinnacleEvent||{};
    const crown=confirmed.crown||confirmed.crownEvent||event||{};
    return `<div class="confirmed-merge-strip"><span class="confirmed-merge-badge">已确认关联</span><div><strong>${crownEsc(crown.league||event?.league||"皇冠联赛")}</strong><span>${crownEsc(crown.home||event?.home||"主队")} vs ${crownEsc(crown.away||event?.away||"客队")}</span></div><small>平博赛事 ${crownEsc(pinnacle.id||confirmed.pinnacleId||"—")}</small></div>`;
  }
  async function crownLoadConfirmed(event,force=false){
    if(!crownFixtures)return;
    const crownId=String(event&&event.id||"");
    if(!crownId||crownConfirmedLoading.has(crownId))return;
    const lastAttempt=Number(crownConfirmedAttempts.get(crownId)||0);
    // The Crown iframe polls once per second; a slower throttle made the
    // paired Pinnacle water visibly lag behind Crown updates.
    if(!force&&Date.now()-lastAttempt<2000)return;
    crownConfirmedAttempts.set(crownId,Date.now());
    crownConfirmedLoading.add(crownId);
    try{
      const {response,payload}=await fetchJson(`/api/merge/confirmed?crownId=${encodeURIComponent(crownId)}`,{cache:"no-store"});
      if(!response.ok)return;
      const item=Array.isArray(payload.items)?payload.items.find(entry=>String(entry?.crownId||entry?.crown?.id||entry?.crownEvent?.id||"")===crownId):null;
      if(item)crownConfirmedById.set(crownId,{...item,sources:item.sources||payload.sources});
      else crownConfirmedById.delete(crownId);
      onChange();
    }catch(error){
      // A missing merge record is expected for ordinary unconfirmed Crown events.
    }finally{
      crownConfirmedLoading.delete(crownId);
    }
  }
  return {
    get: crownConfirmedFor,
    mergeEvent: crownConfirmedCrownEvent,
    quote: crownPinnacleQuoteFor,
    renderMarkets: () => "",
    renderSummary: (confirmed, event) => enabled ? crownConfirmedSummary(confirmed, event) : "",
    load: crownLoadConfirmed,
  };
}
