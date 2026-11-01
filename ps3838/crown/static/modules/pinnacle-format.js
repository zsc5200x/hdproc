export function crownFormatPinnacleLine(value){
  const number=Number(value);
  if(!Number.isFinite(number))return String(value??"—");
  const rounded=number.toFixed(2).replace(/\.?0+$/,"" );
  return rounded==="-0"?"0":rounded;
}
export function crownFormatPinnacleHandicap(value){
  const number=Number(value);
  if(!Number.isFinite(number))return crownFormatPinnacleLine(value);
  if(Math.abs(number)<0.000001)return "0";
  const magnitude=Math.abs(number);
  const steps=Math.round(magnitude*4);
  const sign=number>0?"+":"-";
  // Crown's split quarter notation: 0.25 -> 0/0.5, 0.75 -> 0.5/1.
  if(Math.abs(magnitude*4-steps)<0.000001&&steps%2===1){
    return `${sign}${crownFormatPinnacleLine(magnitude-0.25)}/${crownFormatPinnacleLine(magnitude+0.25)}`;
  }
  return `${sign}${crownFormatPinnacleLine(magnitude)}`;
}
export function crownFormatPinnacleTotal(value){
  const number=Number(value);
  if(!Number.isFinite(number))return crownFormatPinnacleLine(value);
  const steps=Math.round(number*4);
  if(Math.abs(number*4-steps)<0.000001&&Math.abs(steps)%2===1){
    return `${crownFormatPinnacleLine(number-0.25)}/${crownFormatPinnacleLine(number+0.25)}`;
  }
  return crownFormatPinnacleLine(number);
}
export function crownPinnacleTeams(event){
  const home=String(event?.home||"").trim();
  const away=String(event?.away||"").trim();
  if(home||away)return [home||"主队",away||"客队"];
  const parts=String(event?.name||"").split(/\s+VS\s+/i);
  return [parts[0]||"主队",parts[1]||"客队"];
}
export function crownPinnacleQuote(row,event){
  const [home,away]=crownPinnacleTeams(event);
  const market=String(row?.market||"");
  const option=String(row?.option||"");
  if(market==="1"){
    // Pinnacle numbers 1X2 as 0=home, 1=away, 2=draw.
    const labels={"0":`${home} 主胜`,"1":`${away} 客胜`,"2":"和局"};
    return {label:labels[option]||`选项 ${option}`,line:"全场"};
  }
  if(market==="2"){
    const raw=Number(row?.line);
    const side=option==="0"?raw:-raw;
    const role=!Number.isFinite(side)||Math.abs(side)<0.000001?"平手":side<0?"让球":"受让";
    return {label:`${option==="0"?home:away} ${crownFormatPinnacleHandicap(side)}`,line:role};
  }
  if(market==="3"){
    const line=crownFormatPinnacleTotal(row?.line);
    return {label:`${option==="3"?"大":"小"}${line}`,line:"总进球"};
  }
  return {label:`选项 ${option||"—"}`,line:`盘口 ${market||"—"}`};
}
export function crownPinnaclePrice(row){
  if(!row||row.quoted===false)return "—";
  const raw=row.hongKongLabel!=null&&row.hongKongLabel!==""?row.hongKongLabel:row.hongKong;
  if(raw==null||raw==="")return "—";
  const number=Number(raw);
  return Number.isFinite(number)?number.toFixed(3):"—";
}
/**
 * Movement badge for a paired quote, matching the Pinnacle data panel:
 * up is green, down is red, a newly created row is "新盘", flat is omitted.
 * HK and decimal water differ by 1, so the stored decimal delta is the same
 * number in both formats and needs no conversion.
 */
export function crownPinnacleChange(quote){
  if(!quote||!quote.quoted)return null;
  const direction=String(quote.direction||"flat");
  if(direction==="new")return {direction:"new",text:"新盘",delta:null};
  if(direction!=="up"&&direction!=="down")return null;
  const delta=Number(quote.delta);
  if(!Number.isFinite(delta))return null;
  const arrow=direction==="up"?"▲":"▼";
  const value=`${delta>0?"+":""}${delta.toFixed(3)}`;
  return {direction,text:`${arrow} ${value}`,delta};
}
